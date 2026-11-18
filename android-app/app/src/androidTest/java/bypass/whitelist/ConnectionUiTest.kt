package bypass.whitelist

import android.view.ContextThemeWrapper
import android.view.LayoutInflater
import android.view.View
import android.widget.TextView
import androidx.test.platform.app.InstrumentationRegistry
import bypass.whitelist.tunnel.VpnStatus
import bypass.whitelist.ui.MainFragmentView
import org.junit.Assert.*
import org.junit.Test

class ConnectionUiTest {
    @Test fun connectionProgressStaysSimpleAndFailuresRemainVisible() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        instrumentation.runOnMainSync {
            val context = ContextThemeWrapper(instrumentation.targetContext, R.style.Theme_WhitelistBypass)
            val root = LayoutInflater.from(context).inflate(R.layout.fragment_main_screen, null)
            val view = MainFragmentView(root)
            try {
                val headline = root.findViewById<TextView>(R.id.statusHeadline)
                val detail = root.findViewById<TextView>(R.id.statusDetail)
                for (status in listOf(VpnStatus.CONNECTING, VpnStatus.CALL_CONNECTED, VpnStatus.DATACHANNEL_OPEN, VpnStatus.STARTING)) {
                    view.bindStatus(status)
                    assertEquals(context.getString(R.string.status_headline_connecting), headline.text.toString())
                    assertEquals(View.GONE, detail.visibility)
                }
                view.bindStatus(VpnStatus.TUNNEL_ACTIVE)
                assertEquals(context.getString(R.string.status_headline_connected), headline.text.toString())
                view.bindStatus(VpnStatus.TUNNEL_LOST)
                assertEquals(context.getString(R.string.status_headline_disconnected), headline.text.toString())
                view.bindStatus(VpnStatus.CALL_FAILED)
                view.bindStatusText("Fixture connection error")
                assertEquals(View.VISIBLE, detail.visibility)
                assertEquals("Fixture connection error",detail.text.toString())
            } finally { view.detach() }
        }
    }
}
