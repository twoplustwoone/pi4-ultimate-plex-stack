const qbHost = process.env.QBITTORRENT_HOST || "gluetun";
const qbPort = process.env.QBITTORRENT_PORT || "8080";
const qbUser = process.env.QBITTORRENT_USER;
const qbPass = process.env.QBITTORRENT_PASS;
const qbAuth =
  qbUser && qbPass
    ? `${encodeURIComponent(qbUser)}:${encodeURIComponent(qbPass)}@`
    : "";

module.exports = {
  action: "inject",
  useClientTorrents: true,
  torrentClients: [`qbittorrent:http://${qbAuth}${qbHost}:${qbPort}`],
  matchMode: "strict",
  seasonFromEpisodes: null,
  linkDirs: ["/share/downloads"],
};
