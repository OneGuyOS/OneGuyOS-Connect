package org.oneguyos.connect.companion

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import android.os.IBinder
import java.io.DataOutputStream
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.security.SecureRandom
import java.util.concurrent.atomic.AtomicBoolean

/** First LAN preview prototype. Pair authentication and encrypted transport are pending. */
class ScreenShareService : Service() {
    private var projection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private var server: ServerSocket? = null
    private var client: Socket? = null
    private var serverThread: Thread? = null
    private var discoverySocket: DatagramSocket? = null
    private var discoveryThread: Thread? = null
    private var nsdManager: NsdManager? = null
    private var registrationListener: NsdManager.RegistrationListener? = null
    private var pairingPin = ""
    private val active = AtomicBoolean(false)
    @Volatile private var latestJpeg: ByteArray? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> stopSharing()
            ACTION_START -> startSharing(intent)
        }
        return START_NOT_STICKY
    }

    private fun startSharing(intent: Intent) {
        if (active.get()) return
        createNotificationChannel()
        val notification = Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.presence_video_online)
            .setContentTitle("OneGuyOS Connect screen sharing")
            .setContentText("Your phone screen is being shared on your local network.")
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else startForeground(NOTIFICATION_ID, notification)

        val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, 0)
        val resultData = if (Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableExtra(EXTRA_RESULT_DATA, Intent::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra(EXTRA_RESULT_DATA)
        } ?: run { stopSelf(); return }

        val manager = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        projection = manager.getMediaProjection(resultCode, resultData)
        projection?.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                stopSharing()
            }
        }, android.os.Handler(mainLooper))
        val metrics = resources.displayMetrics
        val width = metrics.widthPixels.coerceAtMost(1080)
        val height = (metrics.heightPixels * width / metrics.widthPixels).coerceAtLeast(1)
        imageReader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
        imageReader?.setOnImageAvailableListener({ reader ->
            val image = reader.acquireLatestImage() ?: return@setOnImageAvailableListener
            try {
                val plane = image.planes[0]
                val paddedWidth = width + (plane.rowStride - plane.pixelStride * width) / plane.pixelStride
                val padded = Bitmap.createBitmap(paddedWidth, height, Bitmap.Config.ARGB_8888)
                padded.copyPixelsFromBuffer(plane.buffer)
                val frame = if (paddedWidth == width) padded else Bitmap.createBitmap(padded, 0, 0, width, height)
                val bytes = java.io.ByteArrayOutputStream()
                frame.compress(Bitmap.CompressFormat.JPEG, 65, bytes)
                latestJpeg = bytes.toByteArray()
                if (frame !== padded) frame.recycle()
                padded.recycle()
                bytes.close()
            } catch (_: Exception) {
                // A later frame can recover from a transient rotation/reconfiguration.
            } finally {
                image.close()
            }
        }, null)
        virtualDisplay = projection?.createVirtualDisplay(
            "OneGuyOS Connect", width, height, metrics.densityDpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            imageReader?.surface, null, null
        )
        active.set(true)
        isSharing = true
        pairingPin = "%06d".format(SecureRandom().nextInt(1_000_000))
        startFrameServer()
    }

    private fun startFrameServer() {
        serverThread = Thread {
            try {
                server = ServerSocket(0, 1)
                getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                    .putString(PREF_PIN, pairingPin)
                    .putInt(PREF_PORT, server!!.localPort)
                    .apply()
                advertiseService(server!!.localPort)
                startBroadcastDiscovery(server!!.localPort)
                while (active.get()) {
                    val connected = server?.accept() ?: break
                    client = connected
                    try {
                        connected.use { socket ->
                            socket.soTimeout = 10_000
                            val reader = BufferedReader(InputStreamReader(socket.getInputStream()))
                            if (reader.readLine() != "PIN $pairingPin") {
                                socket.getOutputStream().write("DENIED\n".toByteArray())
                                return@use
                            }
                            socket.getOutputStream().write("OK\n".toByteArray())
                            socket.getOutputStream().flush()
                            socket.soTimeout = 0
                            val output = DataOutputStream(socket.getOutputStream())
                            while (active.get() && !socket.isClosed) {
                                val frame = latestJpeg
                                if (frame == null) Thread.sleep(100)
                                else {
                                    output.writeInt(frame.size)
                                    output.write(frame)
                                    output.flush()
                                    Thread.sleep(80)
                                }
                            }
                        }
                    } catch (_: Exception) {
                        // A viewer may disconnect at any point; keep the listener available for reconnects.
                    } finally {
                        client = null
                    }
                }
            } catch (_: Exception) {
                // Socket close is the normal way to stop the service.
            }
        }.apply { name = "OGConnectFrameServer"; isDaemon = true; start() }
    }

    /** Fallback for Wi-Fi access points that block mDNS multicast between clients. */
    private fun startBroadcastDiscovery(framePort: Int) {
        discoveryThread = Thread {
            try {
                val socket = DatagramSocket(DISCOVERY_PORT, InetAddress.getByName("0.0.0.0"))
                discoverySocket = socket
                val requestBuffer = ByteArray(96)
                while (active.get()) {
                    val request = DatagramPacket(requestBuffer, requestBuffer.size)
                    socket.receive(request)
                    val message = String(request.data, request.offset, request.length, Charsets.UTF_8).trim()
                    if (message != DISCOVERY_QUERY) continue
                    val id = PhoneIdentity.stableId(this@ScreenShareService).replace('|', '-')
                    val manufacturer = Build.MANUFACTURER.replace('|', '-')
                    val model = Build.MODEL.replace('|', '-')
                    val response = "OGCONNECT/1|$id|$manufacturer|$model|$framePort".toByteArray(Charsets.UTF_8)
                    socket.send(DatagramPacket(response, response.size, request.address, request.port))
                }
            } catch (_: Exception) {
                // Socket closure is expected when screen sharing stops.
            }
        }.apply { name = "OGConnectBroadcastDiscovery"; isDaemon = true; start() }
    }

    @Suppress("DEPRECATION")
    private fun advertiseService(port: Int) {
        val info = NsdServiceInfo().apply {
            serviceName = "OneGuyOS Connect Phone"
            serviceType = SERVICE_TYPE
            setPort(port)
            setAttribute("id", PhoneIdentity.stableId(this@ScreenShareService))
            setAttribute("manufacturer", Build.MANUFACTURER.take(120))
            setAttribute("model", "${Build.MANUFACTURER} ${Build.MODEL}".take(120))
        }
        nsdManager = getSystemService(NsdManager::class.java)
        registrationListener = object : NsdManager.RegistrationListener {
            override fun onServiceRegistered(serviceInfo: NsdServiceInfo) = Unit
            override fun onRegistrationFailed(serviceInfo: NsdServiceInfo, errorCode: Int) = Unit
            override fun onServiceUnregistered(serviceInfo: NsdServiceInfo) = Unit
            override fun onUnregistrationFailed(serviceInfo: NsdServiceInfo, errorCode: Int) = Unit
        }
        nsdManager?.registerService(info, NsdManager.PROTOCOL_DNS_SD, registrationListener)
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            val channel = NotificationChannel(CHANNEL_ID, "Screen sharing", NotificationManager.IMPORTANCE_LOW)
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
    }

    private fun stopSharing() {
        active.set(false)
        isSharing = false
        try { registrationListener?.let { nsdManager?.unregisterService(it) } } catch (_: Exception) {}
        registrationListener = null
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().remove(PREF_PIN).remove(PREF_PORT).apply()
        try { client?.close() } catch (_: Exception) {}
        try { server?.close() } catch (_: Exception) {}
        try { discoverySocket?.close() } catch (_: Exception) {}
        discoverySocket = null
        virtualDisplay?.release(); virtualDisplay = null
        imageReader?.close(); imageReader = null
        projection?.stop(); projection = null
        latestJpeg = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() { stopSharing(); super.onDestroy() }

    companion object {
        @Volatile var isSharing: Boolean = false
        const val ACTION_START = "org.oneguyos.connect.companion.START"
        const val ACTION_STOP = "org.oneguyos.connect.companion.STOP"
        const val EXTRA_RESULT_CODE = "result_code"
        const val EXTRA_RESULT_DATA = "result_data"
        private const val CHANNEL_ID = "screen_sharing"
        private const val NOTIFICATION_ID = 2001
        private const val SERVICE_TYPE = "_ogscreen._tcp."
        private const val DISCOVERY_PORT = 43139
        private const val DISCOVERY_QUERY = "OGCONNECT_DISCOVER_V1"
        const val PREFS = "companion_status"
        const val PREF_PIN = "pairing_pin"
        const val PREF_PORT = "screen_port"
    }
}
