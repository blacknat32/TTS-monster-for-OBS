(() => {
    const slots = document.querySelector("#player-slots");
    const status = document.querySelector("#players-status");

    if (!slots || !status) {
        return;
    }

    const updatePlayers = async () => {
        try {
            const response = await fetch(slots.dataset.statusUrl, {
                cache: "no-store",
                headers: { Accept: "application/json" },
            });
            if (!response.ok) {
                throw new Error(`Player status request failed: ${response.status}`);
            }

            const data = await response.json();
            let connectedPlayers = 0;
            for (const player of data.players) {
                const name = slots.querySelector(
                    `[data-player="${player.number}"] [data-player-name]`,
                );
                const voice = slots.querySelector(
                    `[data-player="${player.number}"] [data-player-voice]`,
                );
                if (!name) {
                    continue;
                }
                name.textContent = player.username || "En attente d'un joueur";
                if (voice && document.activeElement !== voice) {
                    voice.value = player.voice_id;
                }
                if (player.username) {
                    connectedPlayers += 1;
                }
            }

            status.textContent =
                connectedPlayers === 0
                    ? "Aucun joueur pour le moment."
                    : `${connectedPlayers} joueur${connectedPlayers > 1 ? "s" : ""} connecte${connectedPlayers > 1 ? "s" : ""}.`;
        } catch (error) {
            console.error("Could not update Twitch player list.", error);
            status.textContent = "Impossible de recuperer les joueurs. Nouvelle tentative...";
        }
    };

    updatePlayers();
    window.setInterval(updatePlayers, 2000);
})();
