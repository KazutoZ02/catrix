async function loadState() {
  const res = await fetch("/api/state");
  const data = await res.json();

  document.getElementById("personality").value = data.personality;

  const ul = document.getElementById("streams");
  ul.innerHTML = "";
  Object.keys(data.streams).forEach(v => {
    const li = document.createElement("li");
    li.textContent = v;
    ul.appendChild(li);
  });
}

async function setPersonality() {
  const value = document.getElementById("personality").value;
  await fetch("/api/personality", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ value })
  });
}

async function addStream() {
  const videoId = document.getElementById("videoId").value;
  await fetch("/api/stream", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ video_id: videoId })
  });
  loadState();
}

loadState();
