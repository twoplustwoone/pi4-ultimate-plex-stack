# Watching Jellyfin at home and away

Jellyfin is the free replacement for Plex. Every app below is free, and so is
the server. Nobody needs a subscription or a plex.tv account.

## The two things to know

**The address.** Every app asks for a server address once. It is the same on
every device.

```text
http://192.168.1.246:8096
```

**Tailscale when away from home.** At home the address just works. Away from
home, open the Tailscale app and switch it on first, then open Jellyfin.
Tailscale is a private tunnel back to the house. It is why the server is not
on the open internet, which is how Plex was broken into in October 2026.

Each person signs in with their own Jellyfin user, so watch progress and
"continue watching" stay separate. The users are **Frisco** and **Bree**. Your
Plex watch history and half-watched spots are already copied in. Change the
starting password in the app under your profile after the first sign-in.

**Signing in on the TV or Shield without typing a password.** Pick **Quick
Connect** on the TV's sign-in screen. It shows a six-character code. On a phone
already signed in, open the app's settings, choose Quick Connect, and enter the
code.

## Phone

Both phones are iPhones. Install **Swiftfin** from the App Store. It plays
almost everything directly. If a title won't play, the official **Jellyfin
Mobile** app is the backup, because it can ask the server to convert the video.

To watch away from home, install **Tailscale** too and sign in once with the
house's Tailscale account. Leave it signed in.

- [ ] Frisco's iPhone 15 plays a movie at home on Wi-Fi.
- [ ] Bree's iPhone 16 plays a movie at home on Wi-Fi.
- [ ] Each plays a movie on cellular with Wi-Fi off and Tailscale on.
- [ ] A show started on the TV resumes at the same point on the phone.

## Laptop

Open the address in a browser and bookmark it. Nothing to install.

Use **Safari** or **Chrome** on the Mac. Most of the library is HEVC video,
which they play directly. Firefox may not, in which case the server converts the
video on the fly. That works and the server handles it easily, but the picture is
slightly softer. If Firefox stutters or looks soft, try Safari before anything else.

Away from home, switch Tailscale on in the menu bar first.

- [ ] Plays a 4K movie at home in Safari or Chrome.
- [ ] Plays a movie away from home with Tailscale on.

## Nvidia Shield

The Shield is the best device in the house for this library. It plays the 4K
HDR files and the lossless movie soundtracks (TrueHD and DTS-HD) without
converting anything.

1. On the Shield, open the Play Store and install **Jellyfin for Android TV**.
2. Enter the address and sign in.
3. In the app's settings, turn on **refresh rate switching**, so films play at
   their own frame rate without judder.
4. If the Shield is connected to a soundbar or receiver, set audio output to
   **passthrough** (bitstream) in the app's settings. Leave it on automatic if
   sound comes from the TV speakers.

The Shield is at home, so it does not need Tailscale.

Dolby Vision is the one known weak spot. Some Dolby Vision files play as plain
HDR10 in this app. They still look good, and it isn't worth chasing unless a
specific title looks wrong.

If the Jellyfin app plays a title badly, note the title and play it in Plex
for now. During the trial period that note is the most useful thing to send
back.

- [ ] Plays a 4K HDR movie with no "transcoding" shown in Jellyfin's dashboard.
- [ ] Plays a TrueHD or DTS-HD movie with the soundbar or receiver showing the format.
- [ ] Subtitles turn on and off.
- [ ] Plays a whole evening of episodes with no buffering.

## The Samsung TV on its own

The TV is a Samsung from around 2021. Samsung's own app store has carried an
official Jellyfin app since February 2026, for 2021 and newer models. So the TV
can most likely play Jellyfin by itself, with no Shield.

1. Find the model year. On the TV, open **Settings**, then **Support**, then
   **About This TV**. The model code's letter after the screen size gives the
   year (for 2021 models it is usually **A**, as in `UN55AU8000`).
2. Open the TV's **Apps** store and search for **Jellyfin**.
3. Install it, enter the address and sign in.

If Jellyfin does not appear in the store, the TV is a 2020 or older model.
Use the Shield on that TV, or a cheap Google TV stick with the same Jellyfin
for Android TV app (around $30 to $100, check current prices).

The TV's app is fine for everyday shows. It plays fewer formats than the
Shield, so the server converts more often, and the lossless movie soundtracks
are where TV apps usually fall short. For films, use the Shield.

The TV's app works only at home. A TV can't run Tailscale.

- [ ] The Samsung's model year is known.
- [ ] Jellyfin installs from the Samsung app store and plays an episode.
- [ ] The same 4K movie is tried on the TV app and on the Shield, and the
  better one is noted.

**Away from home on someone else's TV.** A hotel or a friend's TV can't join
Tailscale. A Google TV or Fire TV stick can, so bring one with both Tailscale
and Jellyfin installed. Casting from a phone doesn't work away from home, because
the TV can't reach the server.

## During the trial period

Plex stays installed for about a month. Use Jellyfin first every time. If
something fails, use Plex and write down the title, the device and what went
wrong. Plex is switched off once a full two weeks go by with nobody needing it.
