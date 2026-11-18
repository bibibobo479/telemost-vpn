package bypass.whitelist.util

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import bypass.whitelist.tunnel.CallConfig
import bypass.whitelist.tunnel.TunnelMode
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.security.KeyStore
import java.security.MessageDigest
import java.util.zip.GZIPInputStream
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

object SharedConnection {
    private const val PREFIX = "https://telemost.yandex.ru/j/shared-"
    private const val ALIAS = "telemost-vpn-share"
    fun isShared(url: String) = url.startsWith(PREFIX)
    private fun bounded(input: InputStream, limit: Int = 65536): ByteArray {
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(4096)
        input.use {
            while (true) {
                val count = it.read(buffer)
                if (count < 0) break
                require(output.size() + count <= limit) { "Response too large" }
                output.write(buffer,0,count)
            }
        }
        return output.toByteArray()
    }
    fun decode(code: String): JSONObject {
        require(code.length <= 32768) { "Code too large" }
        val parts = code.filterNot { it.isWhitespace() }.split('.')
        require(parts.size == 3 && parts[0] == "TMVPN1") { "Invalid code" }
        val digest = MessageDigest.getInstance("SHA-256").digest(parts[1].toByteArray())
            .joinToString("") { "%02x".format(it) }.take(12)
        require(digest == parts[2]) { "Damaged code" }
        val packed = Base64.decode(parts[1],Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
        val value = JSONObject(String(bounded(GZIPInputStream(ByteArrayInputStream(packed)),32768),Charsets.UTF_8))
        require(value.getInt("version") == 1)
        require(Regex("[!-~]{16,4096}").matches(value.getString("token")))
        require(Regex("disk:/Телемост VPN(/[a-f0-9]{32})?").matches(value.getString("folder")))
        val p = value.getJSONObject("profile")
        require(p.get("fps") is Int && p.getInt("fps") in 1..240)
        require(p.get("batch") is Int && p.getInt("batch") in 1..256)
        require(p.get("dual_track") is Boolean)
        return value
    }
    @Synchronized private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(ALIAS,null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(ALIAS,KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    fun importCode(context: Context, code: String, name: String): CallConfig {
        val value = decode(code)
        val p = value.getJSONObject("profile")
        val base = CallConfig.newWith(name.ifBlank { "Telemost VPN" },"")
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE,key()) }
        val encrypted = cipher.doFinal(code.toByteArray())
        val data = Base64.encodeToString(cipher.iv,Base64.NO_WRAP)+":"+Base64.encodeToString(encrypted,Base64.NO_WRAP)
        check(context.getSharedPreferences("shared_connections",Context.MODE_PRIVATE).edit().putString(base.id,data).commit())
        return base.copy(url=PREFIX+base.id,tunnelMode=TunnelMode.VIDEO,vp8Fps=p.getInt("fps"),
            vp8Batch=p.getInt("batch"),dualTrack=p.getBoolean("dual_track"),reliable=true)
    }
    private fun read(context: Context, id: String): JSONObject {
        val data = context.getSharedPreferences("shared_connections",Context.MODE_PRIVATE).getString(id,null)
            ?: error("Connection code missing")
        val parts = data.split(':')
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply {
            init(Cipher.DECRYPT_MODE,key(),GCMParameterSpec(128,Base64.decode(parts[0],Base64.NO_WRAP)))
        }
        return decode(String(cipher.doFinal(Base64.decode(parts[1],Base64.NO_WRAP)),Charsets.UTF_8))
    }
    fun errorMessage(error: Exception): String = when (error) {
        is DiskHttp.Failure -> when (error.status) {
            401,403 -> "Яндекс Диск отклонил токен. Получите новый код подключения"
            404 -> "Файл комнаты не найден на Диске. Проверьте сервер и код"
            429 -> "Яндекс Диск ограничил запросы. Повторите позже"
            else -> "Яндекс Диск: HTTP ${error.status}. Повторите подключение"
        }
        is java.net.UnknownHostException -> "Не удалось найти Яндекс Диск через DNS"
        is java.net.SocketTimeoutException -> "Яндекс Диск не ответил вовремя. Повторите подключение"
        is javax.net.ssl.SSLException -> "Ошибка защищённого соединения с Яндекс Диском"
        is java.security.GeneralSecurityException -> "Не удалось прочитать сохранённый ключ. Импортируйте код заново"
        is java.io.IOException -> "Не удалось связаться с Яндекс Диском. Проверьте сеть"
        else -> "Некорректный ответ Диска или сохранённый код подключения"
    }
    fun resolve(context: Context, config: CallConfig): CallConfig {
        val value=read(context,config.url.removePrefix(PREFIX))
        val path=URLEncoder.encode(value.getString("folder")+"/room.json","UTF-8")
        val info=DiskHttp.json("https://cloud-api.yandex.net/v1/disk/resources/download?path=$path",value.getString("token"))
        val href=URL(info.getString("href"))
        require(href.protocol == "https" && href.userInfo == null &&
            (href.host.endsWith(".yandex.net") || href.host.endsWith(".yandex.ru")))
        val link=DiskHttp.json(href.toString()).getString("link")
        val url=URL(link)
        require(url.protocol == "https" && url.host == "telemost.yandex.ru" && url.userInfo == null &&
            (url.port == -1 || url.port == 443) && url.path.startsWith("/j/") && url.path.length > 3)
        return config.copy(url=link)
    }
}
