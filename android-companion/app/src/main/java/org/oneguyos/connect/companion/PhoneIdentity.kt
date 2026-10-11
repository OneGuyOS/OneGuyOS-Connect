package org.oneguyos.connect.companion

import android.content.Context
import java.util.UUID

object PhoneIdentity {
    fun stableId(context: Context): String {
        val prefs = context.getSharedPreferences("companion_identity", Context.MODE_PRIVATE)
        val existing = prefs.getString("install_id", null)
        if (existing != null) return existing
        val generated = UUID.randomUUID().toString()
        prefs.edit().putString("install_id", generated).apply()
        return generated
    }
}
