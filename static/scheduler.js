// --- Vendor libs via CDN (see scheduler.html) ---
// rrule.js, cronstrue, cron-converter must be loaded globally

let recurrenceRule = 'none'; // 'none' | 'daily' | 'weekday' | 'weekly' | 'monthly' | 'custom'
let customRRule = null;
let editMode = false;
let editingTaskId = null;

const dateInput = document.getElementById('dateInput');
const timeInput = document.getElementById('timeInput');
const recurrenceBtn = document.getElementById('recurrenceBtn');
const recurrenceMenu = document.getElementById('recurrenceMenu');
const previewPill = document.getElementById('previewPill');
const submitBtn = document.getElementById('submitBtn');
const msgInput = document.getElementById('msg');
const enabledInput = document.getElementById('enabled');

function getDtIso() {
  if (!dateInput.value || !timeInput.value) return null;
  const dt = new Date(dateInput.value + 'T' + timeInput.value);
  if (isNaN(dt)) return null;
  //return dt.toISOString();
  return `${dateInput.value}T${timeInput.value}`;
}

function getMinutes(dtIso) { return new Date(dtIso).getMinutes(); }
function getHours(dtIso) { return new Date(dtIso).getHours(); }
function getDOW(dtIso) { return new Date(dtIso).getDay() || 7; }
function getDOM(dtIso) { return new Date(dtIso).getDate(); }

function updatePreview() {
  const dtIso = getDtIso();
  let preview = '';
  let cronOrIso = '';
  if (!dtIso) {
    previewPill.textContent = 'Select date and time';
    submitBtn.disabled = true;
    return;
  }
  switch (recurrenceRule) {
    case 'none':
      preview = 'One-off: ' + new Date(dtIso).toLocaleString();
      cronOrIso = dtIso;
      break;
    case 'daily':
      cronOrIso = `${getMinutes(dtIso)} ${getHours(dtIso)} * * *`;
      preview = 'Every day at ' + timeInput.value;
      break;
    case 'weekday':
      cronOrIso = `${getMinutes(dtIso)} ${getHours(dtIso)} * * 1-5`;
      preview = 'Every weekday (Mon–Fri) at ' + timeInput.value;
      break;
    case 'weekly':
      cronOrIso = `${getMinutes(dtIso)} ${getHours(dtIso)} * * ${getDOW(dtIso)}`;
      preview = 'Every week on ' + new Date(dtIso).toLocaleDateString(undefined,{weekday:'long'}) + ' at ' + timeInput.value;
      break;
    case 'monthly':
      cronOrIso = `${getMinutes(dtIso)} ${getHours(dtIso)} ${getDOM(dtIso)} * *`;
      preview = 'Every month on day ' + getDOM(dtIso) + ' at ' + timeInput.value;
      break;
    case 'custom':
      if (customRRule) {
        cronOrIso = rruleToCron(customRRule, dtIso);
        preview = customRRule.toText();
      } else {
        preview = 'Set up custom recurrence';
        cronOrIso = '';
      }
      break;
  }
  // Use cronstrue for cron preview if not one-off
  if (recurrenceRule !== 'none' && cronOrIso) {
    try { preview = window.cronstrue.toString(cronOrIso); } catch {}
  }
  previewPill.textContent = preview;
  submitBtn.disabled = !cronOrIso || !msgInput.value.trim() || (recurrenceRule === 'none' && new Date(dtIso) < new Date());
  if (recurrenceRule === 'none' && new Date(dtIso) < new Date()) {
    previewPill.textContent += ' (in the past!)';
  }
}

dateInput.addEventListener('change', updatePreview);
timeInput.addEventListener('change', updatePreview);
msgInput.addEventListener('input', updatePreview);

recurrenceBtn.onclick = function(e) {
  recurrenceMenu.style.display = recurrenceMenu.style.display === 'block' ? 'none' : 'block';
};
document.addEventListener('click', function(e) {
  if (!recurrenceMenu.contains(e.target) && e.target !== recurrenceBtn) {
    recurrenceMenu.style.display = 'none';
  }
});
recurrenceMenu.querySelectorAll('li').forEach(li => li.addEventListener('click', e => {
  recurrenceRule = e.target.dataset.rule;
  recurrenceBtn.textContent = e.target.textContent + ' ▾';
  recurrenceMenu.style.display = 'none';
  if (recurrenceRule === 'custom') openCustomModal();
  updatePreview();
}));

function openCustomModal() {
  document.getElementById('customModal').style.display = 'block';
  // Fill #customRecurrenceContent with custom UI (not implemented here)
}
document.getElementById('closeCustomModal').onclick = function() {
  document.getElementById('customModal').style.display = 'none';
};

function rruleToCron(rruleObj, dtIso) {
  // Placeholder: real implementation would map rrule to cron string
  // For demo, fallback to daily
  return `${getMinutes(dtIso)} ${getHours(dtIso)} * * *`;
}

async function fetchTasks() {
  const res = await fetch('/api/tasks');
  const tasks = await res.json();
  renderTasks(tasks);
}

function renderTasks(tasks) {
  let html = `<table class="task-table"><tr><th>ID</th><th>Message</th><th>Cron/ISO</th><th>Next Run</th><th>Enabled</th><th>Actions</th></tr>`;
  for (const t of tasks) {
    html += `<tr>
      <td>${t.id.slice(0,8)}</td>
      <td>${t.message}</td>
      <td>${t.cron}</td>
      <td>${t.next_run ? new Date(t.next_run).toLocaleString() : ''}</td>
      <td><input type="checkbox" ${t.enabled ? 'checked' : ''} onchange="updateTask('${t.id}', {enabled:this.checked})"></td>
      <td>
        <button onclick="editTask('${t.id}')">Edit</button>
        <button onclick="deleteTask('${t.id}')">Delete</button>
      </td>
    </tr>`;
  }
  html += `</table>`;
  document.getElementById('taskTableWrap').innerHTML = html;
}

window.updateTask = async function(id, patch) {
  await fetch(`/api/tasks/${id}`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(patch)});
  fetchTasks();
}

window.deleteTask = async function(id) {
  if (!confirm('Delete this task?')) return;
  await fetch(`/api/tasks/${id}`, {method:'DELETE'});
  fetchTasks();
}

window.editTask = async function(id) {
  const res = await fetch('/api/tasks');
  const tasks = await res.json();
  const t = tasks.find(x=>x.id===id);
  if (!t) return;
  msgInput.value = t.message;
  enabledInput.checked = t.enabled;
  // Parse cron or ISO
  if (/^\d{4}-\d{2}-\d{2}T/.test(t.cron)) {
    // ISO
    recurrenceRule = 'none';
    recurrenceBtn.textContent = 'Doesn’t repeat ▾';
    const dt = new Date(t.cron);
    dateInput.value = dt.toISOString().slice(0,10);
    timeInput.value = dt.toISOString().slice(11,16);
  } else {
    // Try to match cron
    // For simplicity, only handle known patterns here
    const parts = t.cron.split(' ');
    if (parts.length === 5) {
      if (/1-5$/.test(parts[4])) {
        recurrenceRule = 'weekday';
        recurrenceBtn.textContent = 'Every weekday (Mon–Fri) ▾';
      } else if (parts[2] !== '*' && parts[3] === '*' && parts[4] === '*') {
        recurrenceRule = 'monthly';
        recurrenceBtn.textContent = 'Every month ▾';
      } else if (parts[4] !== '*' && parts[2] === '*' && parts[3] === '*') {
        recurrenceRule = 'weekly';
        recurrenceBtn.textContent = 'Every week ▾';
      } else if (parts[2] === '*' && parts[3] === '*' && parts[4] === '*') {
        recurrenceRule = 'daily';
        recurrenceBtn.textContent = 'Every day ▾';
      } else {
        recurrenceRule = 'custom';
        recurrenceBtn.textContent = 'Custom… ▾';
        // Optionally open custom modal and prefill
      }
      // Set date/time from cron (approximate: today with those hours/minutes)
      const now = new Date();
      now.setHours(Number(parts[1]), Number(parts[0]), 0, 0);
      dateInput.value = now.toISOString().slice(0,10);
      timeInput.value = now.toISOString().slice(11,16);
    }
  }
  editingTaskId = t.id;
  editMode = true;
  updatePreview();
}

document.getElementById('taskForm').onsubmit = async function(e) {
  e.preventDefault();
  const msg = msgInput.value;
  const enabled = enabledInput.checked;
  const dtIso = getDtIso();
  let cronOrIso = '';
  switch (recurrenceRule) {
    case 'none':
      cronOrIso = dtIso;
      break;
    case 'daily':
      cronOrIso = `${getMinutes(dtIso)} ${getHours(dtIso)} * * *`;
      break;
    case 'weekday':
      cronOrIso = `${getMinutes(dtIso)} ${getHours(dtIso)} * * 1-5`;
      break;
    case 'weekly':
      cronOrIso = `${getMinutes(dtIso)} ${getHours(dtIso)} * * ${getDOW(dtIso)}`;
      break;
    case 'monthly':
      cronOrIso = `${getMinutes(dtIso)} ${getHours(dtIso)} ${getDOM(dtIso)} * *`;
      break;
    case 'custom':
      cronOrIso = rruleToCron(customRRule, dtIso);
      break;
  }
  if (!cronOrIso) return;
  const userTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const data = {message:msg, cron:cronOrIso, enabled, timezone: userTz };
  if (editMode && editingTaskId) {
    await fetch(`/api/tasks/${editingTaskId}`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    editMode = false; editingTaskId = null;
  } else {
    await fetch('/api/tasks', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  }
  document.getElementById('taskForm').reset();
  recurrenceRule = 'none'; recurrenceBtn.textContent = 'Doesn’t repeat ▾';
  updatePreview();
  fetchTasks();
}

fetchTasks();
updatePreview();
