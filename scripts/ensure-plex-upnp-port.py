#!/usr/bin/env python3
"""Create or renew the router's TCP port mapping for Plex.

The OpenWrt router caps UPnP leases at about 20 minutes, so this script is
intended to run from a systemd timer more frequently than that.
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


SSDP_ADDRESS = ("239.255.255.250", 1900)
SSDP_REQUEST = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST:239.255.255.250:1900\r\n"
    'MAN:"ssdp:discover"\r\n'
    "MX:2\r\n"
    "ST:urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n"
    "\r\n"
).encode()


def discover_gateway(timeout: float = 4.0) -> tuple[str, str]:
    """Return the WAN IP service type and control URL for the LAN gateway."""
    locations: list[str] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.settimeout(0.5)
        sock.sendto(SSDP_REQUEST, SSDP_ADDRESS)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                payload, _ = sock.recvfrom(8192)
            except socket.timeout:
                continue
            for line in payload.decode("utf-8", "replace").split("\r\n"):
                if line.lower().startswith("location:"):
                    location = line.split(":", 1)[1].strip()
                    if location not in locations:
                        locations.append(location)
    finally:
        sock.close()

    candidates: list[tuple[str, str]] = []
    for location in locations:
        try:
            description = urllib.request.urlopen(location, timeout=4).read()
            root = ET.fromstring(description)
        except (OSError, ET.ParseError):
            continue
        for service in root.iter():
            if not service.tag.endswith("service"):
                continue
            values = {child.tag.split("}")[-1]: child.text or "" for child in service}
            service_type = values.get("serviceType", "")
            if "WANIPConnection" not in service_type:
                continue
            control_url = urllib.parse.urljoin(location, values.get("controlURL", ""))
            candidates.append((service_type, control_url))

    if not candidates:
        raise RuntimeError("no UPnP Internet Gateway Device found")
    return sorted(candidates, reverse=True)[0]


def local_address_for(control_url: str) -> str:
    """Find the Pi address on the interface used to reach the gateway."""
    host = urllib.parse.urlparse(control_url).hostname
    if not host:
        raise RuntimeError("gateway control URL has no hostname")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((host, 1900))
        return sock.getsockname()[0]
    finally:
        sock.close()


def soap_request(control_url: str, service_type: str, action: str, body: str) -> str:
    envelope = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{action} xmlns:u="{service_type}">{body}</u:{action}></s:Body>'
        "</s:Envelope>"
    ).encode()
    request = urllib.request.Request(
        control_url,
        data=envelope,
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{service_type}#{action}"',
        },
    )
    try:
        return urllib.request.urlopen(request, timeout=5).read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        payload = error.read().decode("utf-8", "replace")
        match = re.search(r"<errorCode>(.*?)</errorCode>", payload)
        code = match.group(1) if match else str(error.code)
        raise RuntimeError(f"UPnP {action} failed with error {code}") from error


def renew_mapping(external_port: int, internal_port: int, lease_seconds: int) -> None:
    service_type, control_url = discover_gateway()
    internal_client = local_address_for(control_url)
    body = (
        "<NewRemoteHost></NewRemoteHost>"
        f"<NewExternalPort>{external_port}</NewExternalPort>"
        "<NewProtocol>TCP</NewProtocol>"
        f"<NewInternalPort>{internal_port}</NewInternalPort>"
        f"<NewInternalClient>{internal_client}</NewInternalClient>"
        "<NewEnabled>1</NewEnabled>"
        "<NewPortMappingDescription>Plex Media Server</NewPortMappingDescription>"
        f"<NewLeaseDuration>{lease_seconds}</NewLeaseDuration>"
    )
    soap_request(control_url, service_type, "AddPortMapping", body)
    print(
        f"Plex UPnP mapping renewed: TCP {external_port} -> "
        f"{internal_client}:{internal_port}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--external-port", type=int, default=32400)
    parser.add_argument("--internal-port", type=int, default=32400)
    parser.add_argument("--lease-seconds", type=int, default=1800)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        renew_mapping(args.external_port, args.internal_port, args.lease_seconds)
    except (OSError, RuntimeError) as error:
        print(f"Could not renew Plex UPnP mapping: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
