async function update(data) {
  await fetch("/api/update", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data)
  });
}

function show(section) {
  const p = document.getElementById("panel");

  if (section === "welcome") {
    p.innerHTML = `
      <h2>Welcome / Leave</h2>
      <input id="w_ch" placeholder="Channel ID">
      <input id="w_msg" placeholder="Welcome message">
      <button onclick="saveWelcome()">Save</button>
    `;
  }

  if (section === "levels") {
    p.innerHTML = `
      <h2>Level System</h2>
      <input id="l_ch" placeholder="Channel ID">
      <button onclick="saveLevel()">Save</button>
    `;
  }

  if (section === "youtube") {
    p.innerHTML = `
      <h2>YouTube Channels</h2>
      <input id="yt_ch" placeholder="Channel ID">
      <button onclick="addYT()">Add</button>
    `;
  }

  if (section === "live") {
    p.innerHTML = `
      <h2>YouTube Live Interaction</h2>
      <input id="live_id" placeholder="YouTube LIVE Video ID">
      <button onclick="joinLive()">Join Live Chat</button>
    `;
  }
}

function saveWelcome() {
  update({
    welcome: {
      enabled: true,
      channel_id: document.getElementById("w_ch").value,
      message: document.getElementById("w_msg").value
    }
  });
  alert("Welcome updated");
}

function saveLevel() {
  update({
    level: {
      enabled: true,
      channel_id: document.getElementById("l_ch").value
    }
  });
  alert("Level updated");
}

function addYT() {
  const id = document.getElementById("yt_ch").value;
  update({
    yt_channels: {
      [id]: { live: true, videos: true, shorts: true }
    }
  });
  alert("YouTube channel added");
}

function joinLive() {
  const vid = document.getElementById("live_id").value;
  update({
    streams: {
      [vid]: { force_join: true }
    }
  });
  alert("Bot will join live chat");
}