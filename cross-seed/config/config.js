const qbHost = process.env.QBITTORRENT_HOST || "gluetun";
const qbPort = process.env.QBITTORRENT_PORT || "8080";
const qbUser = process.env.QBITTORRENT_USER;
const qbPass = process.env.QBITTORRENT_PASS;
const qbAuth =
  qbUser && qbPass
    ? `${encodeURIComponent(qbUser)}:${encodeURIComponent(qbPass)}@`
    : "";
const prowlarrUrl = process.env.PROWLARR_URL || "http://prowlarr:9696";
const prowlarrKey = process.env.PROWLARR_API_KEY;
const torznab = prowlarrKey
  ? [`${prowlarrUrl.replace(/\/$/, "")}/api/v1/indexer/torznab?apikey=${prowlarrKey}`]
  : [];

module.exports = {
  action: "inject",
  useClientTorrents: true,
  torznab,
  torrentClients: [`qbittorrent:http://${qbAuth}${qbHost}:${qbPort}`],
  matchMode: "strict",
  seasonFromEpisodes: null,
  linkDirs: ["/share/downloads"],
};
