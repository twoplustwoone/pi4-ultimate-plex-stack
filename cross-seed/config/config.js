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

// Comma-separated list of Prowlarr indexer IDs, e.g. "1,2,3"
const indexerIds = (process.env.PROWLARR_INDEXER_IDS || "1")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

const base = prowlarrUrl.replace(/\/$/, "");

const torznab = prowlarrKey
  ? indexerIds.map((id) => `${base}/${id}/api?apikey=${prowlarrKey}`)
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
