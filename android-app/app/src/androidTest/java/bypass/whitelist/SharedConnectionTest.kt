package bypass.whitelist

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import bypass.whitelist.util.SharedConnection
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.ByteArrayOutputStream
import java.security.MessageDigest
import java.util.zip.GZIPOutputStream
import android.util.Base64

@RunWith(AndroidJUnit4::class)
class SharedConnectionTest {
    private fun code(): String {
        val value=JSONObject().put("version",1).put("token","fixture-token-not-real").put("folder","disk:/Телемост VPN")
            .put("profile",JSONObject().put("fps",90).put("batch",120).put("dual_track",true))
        val output=ByteArrayOutputStream()
        GZIPOutputStream(output).use { it.write(value.toString().toByteArray()) }
        val packed=Base64.encodeToString(output.toByteArray(),Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
        val hash=MessageDigest.getInstance("SHA-256").digest(packed.toByteArray()).joinToString("") { "%02x".format(it) }.take(12)
        return "TMVPN1.$packed.$hash"
    }
    @Test fun importsProfileAndEncryptsCode() {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        val code=code()
        val value=SharedConnection.importCode(context,code,"Fixture")
        assertEquals(90,value.vp8Fps)
        assertEquals(120,value.vp8Batch)
        assertEquals(true,value.dualTrack)
        assertEquals(true,value.reliable)
        assertTrue(SharedConnection.isShared(value.url))
        val prefs=context.getSharedPreferences("shared_connections",0)
        val saved=prefs.getString(value.id,"")!!
        assertFalse(saved.contains(code))
        assertFalse(saved.contains("fixture-token"))
        prefs.edit().remove(value.id).commit()
    }
    @Test fun rejectsDamagedCode() {
        try { SharedConnection.decode(code().dropLast(1)); fail("Accepted damaged code") }
        catch (_: IllegalArgumentException) { }
    }
}
