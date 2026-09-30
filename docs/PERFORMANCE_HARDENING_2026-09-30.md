# WSA performance and runtime audit — 2026-09-30

## Verified observations

- WSA Android display can remain `Asleep/OFF` even while ADB is online. In that
  state `screencap` is black and Kingshot may have no PID.
- After `KEYCODE_WAKEUP`, keyguard dismissal, stay-awake and explicit activity
  start, the real game framebuffer and PID are available.
- Host H.264 is not yet accepted: scrcpy-server 4.1 starts, ADB forwarding is
  established and `OMX.google.h264.encoder` is selected, but the current WSA
  build emitted zero encoded frames during the bounded probe.
- PNG fallback measurements were about 303 ms at 1920x1080 before game start,
  about 218 ms at an idle 1280x720 override, and about 350 ms at 1280x720 while
  Kingshot was loading. These numbers describe this host/run only.

## Changes in this release candidate

1. The PNG fallback is capped at 2 FPS.
2. The H.264 profile defaults to 960 px, 2 Mbit/s and 15 FPS with a one-frame
   queue and a single failover instead of an encoder restart loop.
3. While automation is active, it is the sole capture owner. The GUI reads the
   latest replaceable JPEG published by the bot, eliminating the former second
   full-resolution capture/decode path.
4. Deep health probes are less frequent, while lightweight liveness remains
   independent.
5. scrcpy server stdout and stderr are both retained for diagnosis.

## Remaining performance gate

Low-latency video remains pending until a real-host probe receives and decodes
H.264 frames. The product must show `adb-screencap` in the GUI while using PNG;
it must not label that mode as low latency. No encoder or WSA-specific tuning is
accepted without a bounded before/after capture and a non-black frame.
