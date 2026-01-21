async function api(data) {
  await fetch("/api/update", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(data)
  });
}

function setPersonality() {
  api({ personality: document.getElementById("personality").value });
}

function saveWelcome() {
  api({
    welcome: {
      enabled: true,
      channel_id: document.getElementById("welcome_channel").value,
      message: document.getElementById("welcome_msg").value,
      image: document.getElementById("welcome_img").value
    }
  });
}

function saveLeave() {
  api({
    leave: {
      enabled: true,
      channel_id: document.getElementById("leave_channel").value,
      message: document.getElementById("leave_msg").value,
      image: document.getElementById("leave_img").value
    }
  });
}

function saveLevel() {
  api({
    level: {
      enabled: true,
      channel_id: document.getElementById("level_channel").value,
      xp_per_message: Number(document.getElementById("level_xp").value),
      message: document.getElementById("level_msg").value,
      image: document.getElementById("level_img").value
    }
  });
}

function addYT() {
  api({
    yt_channels: {
      [document.getElementById("yt_id").value]: {
        live: document.getElementById("yt_live").checked,
        videos: document.getElementById("yt_video").checked,
        shorts: document.getElementById("yt_shorts").checked
      }
    }
  });
}