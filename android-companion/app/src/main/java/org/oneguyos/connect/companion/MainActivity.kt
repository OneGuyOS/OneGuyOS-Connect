package org.oneguyos.connect.companion

import android.app.Activity
import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.graphics.Typeface
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

class MainActivity : Activity() {
    private lateinit var status: TextView
    private lateinit var pairingCodeLabel: TextView
    private lateinit var pairingCode: TextView
    private lateinit var presence: PresenceAnnouncer
    private val handler = Handler(Looper.getMainLooper())
    private val statusRefresh = object : Runnable {
        override fun run() {
            refreshShareStatus()
            handler.postDelayed(this, 1_000)
        }
    }

    @Deprecated("Android system consent activity callback")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQUEST_CAPTURE && resultCode == Activity.RESULT_OK && data != null) {
            val service = Intent(this, ScreenShareService::class.java).apply {
                action = ScreenShareService.ACTION_START
                putExtra(ScreenShareService.EXTRA_RESULT_CODE, resultCode)
                putExtra(ScreenShareService.EXTRA_RESULT_DATA, data)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) startForegroundService(service)
            else startService(service)
            status.text = "Starting screen sharing on your local network…"
            handler.postDelayed({ refreshShareStatus() }, 700)
        } else {
            status.text = "Screen sharing was not approved. The phone remains connected."
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        presence = PresenceAnnouncer(this)
        presence.start()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(32, 32, 32, 32)
        }
        root.addView(TextView(this).apply {
            text = "OneGuyOS Connect"
            textSize = 26f
            gravity = Gravity.CENTER
        })
        root.addView(TextView(this).apply {
            text = "Pairing and screen sharing are being set up. Screen sharing starts only when you tap the button and approve Android’s prompt."
            textSize = 16f
            gravity = Gravity.CENTER
            setPadding(0, 20, 0, 20)
        })
        status = TextView(this).apply {
            text = "Phone identity: ${Build.MANUFACTURER} ${Build.MODEL}"
            textSize = 14f
            gravity = Gravity.CENTER
            setPadding(0, 12, 0, 20)
        }
        root.addView(status)
        pairingCodeLabel = TextView(this).apply {
            text = "PAIRING CODE"
            textSize = 14f
            gravity = Gravity.CENTER
            setPadding(0, 8, 0, 0)
            visibility = android.view.View.GONE
        }
        root.addView(pairingCodeLabel)
        pairingCode = TextView(this).apply {
            textSize = 40f
            typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
            setTextIsSelectable(true)
            setPadding(0, 0, 0, 16)
            visibility = android.view.View.GONE
        }
        root.addView(pairingCode)
        root.addView(Button(this).apply {
            text = "Share phone screen"
            setOnClickListener {
                val manager = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
                @Suppress("DEPRECATION")
                startActivityForResult(manager.createScreenCaptureIntent(), REQUEST_CAPTURE)
            }
        })
        root.addView(Button(this).apply {
            text = "Stop sharing"
            setOnClickListener {
                startService(Intent(this@MainActivity, ScreenShareService::class.java).apply {
                    action = ScreenShareService.ACTION_STOP
                })
                status.text = "Screen sharing stopped."
            }
        })
        setContentView(root)
    }

    override fun onResume() {
        super.onResume()
        handler.removeCallbacks(statusRefresh)
        handler.post(statusRefresh)
    }

    override fun onPause() {
        handler.removeCallbacks(statusRefresh)
        super.onPause()
    }

    override fun onDestroy() {
        presence.stop()
        super.onDestroy()
    }

    private fun refreshShareStatus() {
        val prefs = getSharedPreferences(ScreenShareService.PREFS, MODE_PRIVATE)
        val pin = prefs.getString(ScreenShareService.PREF_PIN, null)
        if (pin != null && ScreenShareService.isSharing) {
            status.text = "Screen sharing is active. Enter this code on your computer.\nKeep this phone on the same Wi-Fi as your computer."
            pairingCode.text = pin
            pairingCodeLabel.visibility = android.view.View.VISIBLE
            pairingCode.visibility = android.view.View.VISIBLE
        } else {
            pairingCodeLabel.visibility = android.view.View.GONE
            pairingCode.visibility = android.view.View.GONE
            if (pin != null || status.text.toString().startsWith("Screen sharing is active")) {
                status.text = "Screen sharing is not active. Tap Share Phone Screen and approve Android’s prompt to start a new session."
            }
        }
    }

    companion object { private const val REQUEST_CAPTURE = 401 }
}
