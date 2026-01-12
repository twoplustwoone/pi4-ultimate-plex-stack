module.exports = {
  action: "inject",
  useClientTorrents: true,
  torrentClients: [
    {
      type: "qbittorrent",
      host: process.env.QBITTORRENT_HOST || "gluetun",
      port: Number(process.env.QBITTORRENT_PORT || 8080),
      username: process.env.QBITTORRENT_USER,
      password: process.env.QBITTORRENT_PASS,
    },
  ],
  matchMode: "strict",
  seasonFromEpisodes: null,
  linkDirs: ["/share/downloads"],
};
