import time
import uuid
import supervisor as s

old = s.disk(s.ROOM)['link']
assert s.valid_link(old), 'Invalid published room'
print('PASS: private room discovery via Disk', flush=True)
s.disk(s.REFRESH, {'id': str(uuid.uuid4()), 'time': time.time()})
deadline = time.monotonic() + 210
new = old
while time.monotonic() < deadline:
    time.sleep(5)
    try:
        new = s.disk(s.ROOM)['link']
    except (OSError, ValueError):
        continue
    if s.valid_link(new) and new != old:
        break
assert new != old and s.valid_link(new), 'Server did not rotate room'
print('PASS: server created and published a new room', flush=True)
while time.monotonic() < deadline:
    if (s.STATE / 'call.txt').read_text().strip() == new:
        print('PASS: client discovered the new room automatically', flush=True)
        break
    time.sleep(5)
else:
    raise RuntimeError('Client did not discover new room')
