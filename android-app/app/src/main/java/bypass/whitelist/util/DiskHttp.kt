package bypass.whitelist.util

import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL

/** Yandex signed download URLs redirect to storage. OAuth is only sent to the API. */
object DiskHttp {
    class Failure(val status: Int) : Exception("Disk HTTP $status")

    fun trustedStorage(url: URL): Boolean = url.protocol == "https" && url.userInfo == null &&
        (url.port == -1 || url.port == 443) &&
        (url.host.endsWith(".yandex.net") || url.host.endsWith(".yandex.ru"))

    fun json(address: String, token: String? = null,
             open: (URL) -> HttpURLConnection = { it.openConnection() as HttpURLConnection }): JSONObject {
        var url=URL(address)
        if (token != null) require(url.protocol == "https" && url.host == "cloud-api.yandex.net" &&
            url.userInfo == null && (url.port == -1 || url.port == 443))
        else require(trustedStorage(url))
        repeat(6) { hop ->
            val conn=open(url)
            conn.connectTimeout=20000; conn.readTimeout=25000; conn.instanceFollowRedirects=false
            if (token != null && hop == 0) conn.setRequestProperty("Authorization","OAuth $token")
            try {
                val status=conn.responseCode
                if (status in listOf(301,302,303,307,308)) {
                    // Do not redirect an authenticated API request or forward its token.
                    check(token == null && hop < 5) { "Unexpected Disk redirect" }
                    val location=conn.getHeaderField("Location") ?: error("Missing redirect destination")
                    val next=URL(url,location)
                    require(trustedStorage(next)) { "Unexpected storage host" }
                    url=next
                } else {
                    if (status != 200) throw Failure(status)
                    val out=ByteArrayOutputStream()
                    conn.inputStream.use { input ->
                        val buffer=ByteArray(4096)
                        while (true) {
                            val count=input.read(buffer)
                            if (count < 0) break
                            require(out.size()+count <= 65536) { "Response too large" }
                            out.write(buffer,0,count)
                        }
                    }
                    return JSONObject(String(out.toByteArray(),Charsets.UTF_8))
                }
            } finally { conn.disconnect() }
        }
        error("Too many Disk redirects")
    }
}
