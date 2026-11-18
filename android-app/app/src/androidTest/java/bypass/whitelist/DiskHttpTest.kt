package bypass.whitelist

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import bypass.whitelist.util.DiskHttp
import bypass.whitelist.util.SharedConnection
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.net.HttpURLConnection
import java.net.URL
import java.io.File

@RunWith(AndroidJUnit4::class)
class DiskHttpTest {
    private class Reply(url: URL, val status: Int, val location: String? = null): HttpURLConnection(url) {
        var closed = false
        override fun connect() {}
        override fun disconnect() { closed = true }
        override fun usingProxy() = false
        override fun getResponseCode() = status
        override fun getHeaderField(name: String) = if (name == "Location") location else null
        override fun getInputStream() = "{\"link\":\"ok\"}".byteInputStream()
    }
    @Test fun followsStorageRedirectWithoutAuthorization() {
        val replies = mutableListOf<Reply>()
        val result = DiskHttp.json("https://downloader.disk.yandex.ru/file") { url ->
            Reply(url, if (replies.isEmpty()) 302 else 200, "https://mds.storage.yandex.net/file").also { replies.add(it) }
        }
        assertEquals("ok", result.getString("link"))
        assertEquals(2, replies.size)
        replies.forEach { assertTrue(it.closed); assertNull(it.getRequestProperty("Authorization")) }
    }
    @Test fun rejectsUnsafeRedirectsAndAuthenticatedRedirects() {
        for (destination in listOf("http://mds.storage.yandex.net/file", "https://evil.example/file", "https://yandex.net.evil.example/file")) {
            var count = 0
            try {
                DiskHttp.json("https://downloader.disk.yandex.ru/file") { url -> count++; Reply(url,302,destination) }
                fail("Unsafe redirect accepted")
            } catch (_: IllegalArgumentException) {}
            assertEquals(1,count)
        }
        var count = 0
        try {
            DiskHttp.json("https://cloud-api.yandex.net/file", "fixture-token") { url ->
                count++; Reply(url,302,"https://mds.storage.yandex.net/file")
            }
            fail("Authenticated redirect accepted")
        } catch (_: IllegalStateException) {}
        assertEquals(1,count)
    }
    @Test fun boundsRedirectLoops() {
        var count = 0
        try {
            DiskHttp.json("https://downloader.disk.yandex.ru/file") { url -> count++; Reply(url,302,"/file") }
            fail("Loop accepted")
        } catch (_: IllegalStateException) {}
        assertEquals(6,count)
    }
    @Test fun liveDiscoveryWhenFixtureProvided() {
        val path = InstrumentationRegistry.getArguments().getString("liveCodeFile")
        org.junit.Assume.assumeTrue(path != null)
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val config = SharedConnection.importCode(context, File(path!!).readText().trim(), "Live discovery test")
        try {
            val resolved = SharedConnection.resolve(context,config)
            assertFalse(SharedConnection.isShared(resolved.url))
            assertTrue(resolved.url.startsWith("https://telemost.yandex.ru/j/"))
        } finally { context.getSharedPreferences("shared_connections",0).edit().remove(config.id).commit() }
    }
}
