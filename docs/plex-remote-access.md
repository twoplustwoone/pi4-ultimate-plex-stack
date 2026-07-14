# Plex remote access on the Pi

Plex listens on TCP port 32400 on the Pi. Direct remote access also requires
the router to forward a public TCP port to that listener; Plex Pass or Remote
Watch Pass only provides playback entitlement and does not create the network
route.

The OpenWrt router on this network caps dynamic UPnP mappings at roughly 20
minutes. The included systemd timer renews the mapping every five minutes:

```bash
sudo cp systemd/plex-upnp-port.service systemd/plex-upnp-port.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now plex-upnp-port.timer
```

Plex itself must be configured with Remote Access enabled, **Manually specify
public port** selected, and public port `32400`. The timer discovers the local
UPnP gateway and current Pi LAN address, then renews this route:

```text
WAN TCP 32400 -> Pi TCP 32400
```

Check the timer and its most recent renewal with:

```bash
systemctl status plex-upnp-port.timer
journalctl -u plex-upnp-port.service -n 20 --no-pager
```

A static OpenWrt port-forward and DHCP reservation are still preferable if
router administrator access becomes available. They remove the dependency on
UPnP and this renewal timer.
