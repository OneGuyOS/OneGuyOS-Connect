package org.oneguyos.connect.companion

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import java.net.ServerSocket
import java.util.concurrent.atomic.AtomicBoolean

/** Advertises this phone while the companion app is open; it does not wake the screen. */
class PresenceAnnouncer(private val context: Context) {
    private val active = AtomicBoolean(false)
    private var server: ServerSocket? = null
    private var manager: NsdManager? = null
    private var listener: NsdManager.RegistrationListener? = null
    private var worker: Thread? = null

    fun start() {
        if (!active.compareAndSet(false, true)) return
        worker = Thread {
            try {
                server = ServerSocket(0, 4)
                advertise(server!!.localPort)
                while (active.get()) {
                    val socket = server?.accept() ?: break
                    socket.use {
                        val id = PhoneIdentity.stableId(context)
                        val model = "${Build.MANUFACTURER} ${Build.MODEL}"
                        it.getOutputStream().write("OGCONNECT/1 $id $model\n".toByteArray())
                    }
                }
            } catch (_: Exception) {
                // Closing the listener socket is the normal stop path.
            }
        }.apply { name = "OGConnectPhonePresence"; isDaemon = true; start() }
    }

    @Suppress("DEPRECATION")
    private fun advertise(port: Int) {
        val id = PhoneIdentity.stableId(context)
        val info = NsdServiceInfo().apply {
            serviceName = "OGConnect-$id"
            serviceType = SERVICE_TYPE
            setPort(port)
            setAttribute("id", id)
            setAttribute("manufacturer", Build.MANUFACTURER.take(120))
            setAttribute("model", "${Build.MANUFACTURER} ${Build.MODEL}".take(120))
        }
        manager = context.getSystemService(NsdManager::class.java)
        listener = object : NsdManager.RegistrationListener {
            override fun onServiceRegistered(serviceInfo: NsdServiceInfo) = Unit
            override fun onRegistrationFailed(serviceInfo: NsdServiceInfo, errorCode: Int) = Unit
            override fun onServiceUnregistered(serviceInfo: NsdServiceInfo) = Unit
            override fun onUnregistrationFailed(serviceInfo: NsdServiceInfo, errorCode: Int) = Unit
        }
        manager?.registerService(info, NsdManager.PROTOCOL_DNS_SD, listener)
    }

    fun stop() {
        if (!active.getAndSet(false)) return
        try { listener?.let { manager?.unregisterService(it) } } catch (_: Exception) {}
        listener = null
        try { server?.close() } catch (_: Exception) {}
        server = null
    }

    companion object { private const val SERVICE_TYPE = "_ogconnect._tcp." }
}
