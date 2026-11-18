package rtc

import (
	"bytes"
	kcp "github.com/xtaci/kcp-go/v5"
	"testing"
	"whitelist-bypass/relay/common"
	"whitelist-bypass/relay/tunnel"
)

func TestKCPPayloadFitsOnePacket(t *testing.T) {
	carrier := &MultiTrackKCPTunnel{}
	for _, tc := range []struct {
		name          string
		size, packets int
	}{
		{"legacy", common.VP8BufSize, 2},
		{"sized", carrier.MaxPayloadSize(), 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var packets [][]byte
			sender := kcp.NewKCP(1, func(b []byte, n int) { packets = append(packets, append([]byte(nil), b[:n]...)) })
			sender.SetMtu(kcpSegmentMTU)
			sender.NoDelay(1, 10, 2, 1)
			frame := tunnel.EncodeFrame(7, tunnel.MsgData, bytes.Repeat([]byte{42}, tc.size))
			sender.Send(frame)
			sender.Update()
			if len(packets) != tc.packets {
				t.Fatalf("got %d packets, want %d", len(packets), tc.packets)
			}
			receiver := kcp.NewKCP(1, func([]byte, int) {})
			for _, packet := range packets {
				receiver.Input(packet, kcp.IKCP_PACKET_REGULAR, true)
			}
			got := make([]byte, len(frame))
			if n := receiver.Recv(got); n != len(frame) || !bytes.Equal(got, frame) {
				t.Fatal("payload did not round trip")
			}
		})
	}
}
