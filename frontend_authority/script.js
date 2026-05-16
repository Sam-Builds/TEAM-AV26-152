// Disaster dashboard script - cleaned and fixed
// Loads disasters from backend, shows modal, broadcasts alerts

// Mock fallback data
const mockDisasters = [
  { id: 1, title: 'Wildfire - Forest Zone Alpha', type: 'Wildfire', location: 'North Forest District', description: 'Large wildfire spreading rapidly.', severity: 'critical', timestamp: new Date().toISOString(), latitude: 0, longitude: 0, affectedPeople: 500, status: 'Active' },
  { id: 2, title: 'Flooding - River Basin', type: 'Flooding', location: 'Central River Valley', description: 'River overflow after heavy rains.', severity: 'critical', timestamp: new Date().toISOString(), latitude: 0, longitude: 0, affectedPeople: 1200, status: 'Active' }
];

// State
let disasters = [];
let currentSelectedDisaster = null;

// Elements
const apiEndpoint = document.getElementById('apiEndpoint');
const refreshBtn = document.getElementById('refreshBtn');
const disastersList = document.getElementById('disastersList');
const statusEl = document.getElementById('status');
const modal = document.getElementById('alertModal');
const closeBtn = document.querySelector('.close');
const confirmAlertBtn = document.getElementById('confirmAlertBtn');
const cancelAlertBtn = document.getElementById('cancelAlertBtn');
const toast = document.getElementById('toast');

// Initialize
document.addEventListener('DOMContentLoaded', () => {
  if (!apiEndpoint.value || !apiEndpoint.value.trim()) apiEndpoint.value = 'https://api.samstack.site';
  setupEventListeners();
  registerServiceWorker();
  loadDisasters();
  // start live sidebar polling
  startSidebarPolling();
});

function setupEventListeners() {
  if (refreshBtn) refreshBtn.addEventListener('click', loadDisasters);
  if (closeBtn) closeBtn.addEventListener('click', closeModal);
  if (cancelAlertBtn) cancelAlertBtn.addEventListener('click', closeModal);
  if (confirmAlertBtn) confirmAlertBtn.addEventListener('click', sendAlert);
  if (modal) modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
  const notificationsBtn = document.getElementById('notificationsBtn');
  if (notificationsBtn) notificationsBtn.addEventListener('click', enableNotifications);
}

async function loadDisasters() {
  setStatus('Loading disasters...', 'loading');
  if (refreshBtn) refreshBtn.disabled = true;

  try {
    const endpoint = (apiEndpoint?.value || 'https://api.samstack.site').replace(/\/$/, '');
    console.log('Loading disasters from', endpoint);

    // Try the DB rows endpoint first
    const rowsRes = await fetch(`${endpoint}/db/rows?table=disasters`);
    if (rowsRes.ok) {
      const rows = await rowsRes.json();
      if (Array.isArray(rows) && rows.length > 0) {
        disasters = rows.map((r) => ({
          id: r.id ?? r.clusterId ?? Math.random(),
          title: r.title ?? r.type ?? r.primaryThreat ?? 'Unknown Disaster',
          type: r.type ?? r.primaryThreat ?? 'Unknown',
          location: r.location ?? (r.latitude && r.longitude ? `Lat: ${r.latitude}, Lng: ${r.longitude}` : 'Unknown'),
          description: r.description ?? r.summary ?? '',
          severity: (r.severity && r.severity >= 7) ? 'critical' : ((r.severity && r.severity >= 4) ? 'moderate' : (r.severity ? 'low' : 'low')),
          timestamp: r.created_at ?? r.timestamp ?? new Date().toISOString(),
          latitude: r.latitude ?? 0,
          longitude: r.longitude ?? 0,
          affectedPeople: r.affectedPeople ?? 0,
          status: r.status ?? 'Active'
        }));
        console.log('Disasters loaded (db rows):', disasters.length);
        showToast(`Loaded ${disasters.length} disasters`, 'success');
      } else {
        // fallback to calling intelligence endpoint if available
        await loadDisastersFromAlertsEndpoint(endpoint);
      }
    } else {
      // Try alternate endpoint
      await loadDisastersFromAlertsEndpoint(endpoint);
    }
  } catch (err) {
    console.error('loadDisasters error', err);
    showToast('Failed to load disasters, using mock data', 'error');
    disasters = mockDisasters.slice();
  } finally {
    renderDisasters();
    updateStats();
    setStatus('Ready', 'success');
    if (refreshBtn) refreshBtn.disabled = false;
  }
}

async function loadDisastersFromAlertsEndpoint(endpoint) {
  try {
    const res = await fetch(`${endpoint}/api/alerts`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ Query: '', Mode: 'load_mock' }) });
    if (!res.ok) throw new Error(`alerts endpoint ${res.status}`);
    const body = await res.json();
    if (body?.success && body.data?.threatClusters) {
      disasters = body.data.threatClusters.map((tc, i) => ({
        id: tc.clusterId ?? i,
        title: tc.primaryThreat ?? 'Threat',
        type: tc.primaryThreat ?? 'Unknown',
        location: tc.dangerPolygon && tc.dangerPolygon.length ? `Lat: ${tc.dangerPolygon[0].lat}, Lng: ${tc.dangerPolygon[0].lng}` : 'Unknown',
        description: tc.summary ?? '',
        severity: (tc.aggregatedSeverity > 7) ? 'critical' : (tc.aggregatedSeverity > 4 ? 'moderate' : 'low'),
        timestamp: new Date().toISOString(),
        latitude: tc.dangerPolygon && tc.dangerPolygon.length ? tc.dangerPolygon[0].lat : 0,
        longitude: tc.dangerPolygon && tc.dangerPolygon.length ? tc.dangerPolygon[0].lng : 0,
        affectedPeople: tc.sourcePostCount ? tc.sourcePostCount * 10 : 0,
        status: 'Active'
      }));
      showToast(`Loaded ${disasters.length} disasters (intel)`, 'success');
    } else {
      showToast('No disasters returned, using mock data', 'info');
      disasters = mockDisasters.slice();
    }
  } catch (err) {
    console.warn('Failed alerts endpoint, using mock', err);
    disasters = mockDisasters.slice();
  }
}

function renderDisasters() {
  if (!disastersList) return;
  disastersList.innerHTML = '';
  if (!disasters || disasters.length === 0) {
    disastersList.innerHTML = '<div class="loading">No disasters found</div>';
    return;
  }
  disasters.forEach((d) => disastersList.appendChild(createDisasterCard(d)));
}

function createDisasterCard(disaster) {
  const card = document.createElement('div');
  card.className = `disaster-card ${disaster.severity || 'low'}`;
  const timeAgo = formatTimeAgo(new Date(disaster.timestamp));
  const severityBadgeClass = `severity-${disaster.severity || 'low'}`;
  const severityText = (disaster.severity || 'low').toUpperCase();
  card.innerHTML = `
    <div class="disaster-card-header">
      <div>
        <h3>${escapeHtml(disaster.title)}</h3>
        <small style="color:#999">${timeAgo}</small>
      </div>
      <span class="severity-badge ${severityBadgeClass}">${severityText}</span>
    </div>
    <p>${escapeHtml(disaster.description || '')}</p>
    <div class="disaster-info">
      <p><strong>Type:</strong> ${escapeHtml(disaster.type || '')}</p>
      <p><strong>Location:</strong> ${escapeHtml(disaster.location || '')}</p>
      <p><strong>Status:</strong> ${escapeHtml(disaster.status || '')}</p>
      <p><strong>Affected People:</strong> ~${disaster.affectedPeople ?? 0}</p>
    </div>
    <div class="disaster-actions">
      <button class="btn-notify" data-id="${disaster.id}">Send Alert</button>
    </div>`;
  const btn = card.querySelector('.btn-notify');
  if (btn) btn.addEventListener('click', () => openAlertModal(disaster.id));
  return card;
}

function openAlertModal(disasterId) {
  currentSelectedDisaster = disasters.find((d) => d.id === disasterId);
  if (!currentSelectedDisaster) { showToast('Disaster not found', 'error'); return; }
  document.getElementById('modalDisasterType').textContent = currentSelectedDisaster.type || 'N/A';
  document.getElementById('modalLocation').textContent = currentSelectedDisaster.location || 'N/A';
  document.getElementById('modalStatus').textContent = currentSelectedDisaster.status || 'N/A';
  document.getElementById('alertTitle').textContent = currentSelectedDisaster.title;
  document.getElementById('alertBody').textContent = currentSelectedDisaster.description || '';
  const payload = { Query: currentSelectedDisaster.title, Mode: currentSelectedDisaster.severity || 'moderate', Description: currentSelectedDisaster.description || 'Emergency alert issued for the selected incident.', UserLat: currentSelectedDisaster.latitude || null, UserLng: currentSelectedDisaster.longitude || null };
  const payloadEl = document.getElementById('payloadPreview'); if (payloadEl) payloadEl.textContent = JSON.stringify(payload, null, 2);
  if (modal) modal.classList.add('show');
}

function closeModal() { if (modal) modal.classList.remove('show'); currentSelectedDisaster = null; }

async function sendAlert() {
  if (!currentSelectedDisaster) { showToast('No disaster selected', 'error'); return; }
  if (confirmAlertBtn) confirmAlertBtn.disabled = true;
  const originalText = confirmAlertBtn ? confirmAlertBtn.textContent : 'Sending...';
  if (confirmAlertBtn) confirmAlertBtn.textContent = 'Sending...';
  try {
    const endpoint = (apiEndpoint?.value || 'https://api.samstack.site').replace(/\/$/, '');
    const payload = { Query: currentSelectedDisaster.title || 'Disaster Alert', Mode: currentSelectedDisaster.severity || 'moderate', Description: currentSelectedDisaster.description || 'Emergency alert issued for the selected incident.', UserLat: currentSelectedDisaster.latitude || null, UserLng: currentSelectedDisaster.longitude || null };
    console.log('Broadcasting to', endpoint, payload);
    const res = await fetch(`${endpoint}/api/broadcast-alert`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const body = await res.json().catch(() => ({}));
    if (res.ok) {
      showToast(body?.message ?? 'Alert broadcasted', 'success');
      closeModal();
    } else {
      console.error('Broadcast failed', body);
      showToast(body?.message ?? `Broadcast failed: ${res.status}`, 'error');
    }
  } catch (err) {
    console.error('sendAlert error', err);
    showToast('Network error sending alert', 'error');
  } finally {
    if (confirmAlertBtn) { confirmAlertBtn.disabled = false; confirmAlertBtn.textContent = originalText; }
  }
}

function updateStats() {
  const total = disasters.length;
  const critical = disasters.filter(d => d.severity === 'critical').length;
  const moderate = disasters.filter(d => d.severity === 'moderate').length;
  const low = total - critical - moderate;
  document.getElementById('totalDisasters').textContent = total;
  document.getElementById('criticalCount').textContent = critical;
  document.getElementById('moderateCount').textContent = moderate;
  document.getElementById('lowCount').textContent = low;
}

function setStatus(message, type = 'info') { if (!statusEl) return; statusEl.textContent = message; statusEl.className = `status ${type}`; }

function showToast(message, type = 'info') { if (!toast) return; toast.textContent = message; toast.className = `toast ${type} show`; setTimeout(() => toast.classList.remove('show'), 4000); }

function formatTimeAgo(date) { const now = new Date(); const seconds = Math.floor((now - date) / 1000); if (seconds < 60) return 'just now'; if (seconds < 3600) return `${Math.floor(seconds/60)}m ago`; if (seconds < 86400) return `${Math.floor(seconds/3600)}h ago`; return `${Math.floor(seconds/86400)}d ago`; }

function escapeHtml(text) { const d = document.createElement('div'); d.textContent = text ?? ''; return d.innerHTML; }

function registerServiceWorker() { if ('serviceWorker' in navigator) { navigator.serviceWorker.register('firebase-messaging-sw.js').then(reg => console.log('SW registered', reg)).catch(err => console.warn('SW failed', err)); } }

async function enableNotifications() { try { if (!('Notification' in window)) { showToast('Browser does not support notifications', 'error'); return; } if (typeof firebase === 'undefined' || !firebase.messaging) { showToast('Firebase not configured', 'error'); return; } const perm = await Notification.requestPermission(); if (perm === 'granted' && typeof initializeFirebaseMessaging === 'function') initializeFirebaseMessaging(); } catch (e) { console.error(e); showToast('Failed to enable notifications', 'error'); } }

// End of script

// ---------------------- Live Sidebar Polling ----------------------
const sidebarHost = 'http://172.20.1.149:8000';
let seenStats = new Set();
let seenTrends = new Set();
let sidebarIntervalId = null;

function startSidebarPolling() {
  // initial fetch
  pollSidebar();
  // poll every 8 seconds
  sidebarIntervalId = setInterval(pollSidebar, 8000);
}

async function pollSidebar() {
  try {
    await fetchAndAppend(`${sidebarHost}/alerts/stats`, 'liveStatsList', seenStats);
  } catch (e) { console.warn('stats fetch failed', e); }
  try {
    await fetchAndAppend(`${sidebarHost}/alerts/trends`, 'liveTrendsList', seenTrends);
  } catch (e) { console.warn('trends fetch failed', e); }
}

async function fetchAndAppend(url, listId, seenSet) {
  const listEl = document.getElementById(listId);
  if (!listEl) return;
  try {
    const res = await fetch(url, { method: 'GET' });
    if (!res.ok) throw new Error(`status ${res.status}`);
    const data = await res.json();

    // normalize into array of items
    let items = [];
    if (Array.isArray(data)) items = data;
    else if (data && Array.isArray(data.items)) items = data.items;
    else if (data && typeof data === 'object') {
      // if object with numeric keys or single item
      items = Object.values(data);
    }

    if (!items || items.length === 0) return;

    // remove placeholder
    const placeholder = listEl.querySelector('.live-empty');
    if (placeholder) placeholder.remove();

    for (const it of items) {
      // compute unique id for item
      const id = it?.id ?? it?.name ?? JSON.stringify(it);
      if (seenSet.has(id)) continue;
      seenSet.add(id);
      renderLiveItem(listEl, it);
    }
  } catch (err) {
    console.warn('fetchAndAppend error', url, err);
  }
}

function renderLiveItem(listEl, item) {
  const li = document.createElement('li');
  li.className = 'live-item';
  // prefer friendly fields
  let main = '';
  if (!item) main = JSON.stringify(item);
  else if (typeof item === 'string') main = item;
  else if (item.title) main = item.title;
  else if (item.name) main = item.name;
  else if (item.message) main = item.message;
  else if (item.summary) main = item.summary;
  else main = JSON.stringify(item);

  li.innerHTML = `<div class="live-main">${escapeHtml(String(main))}</div><div class="live-item-time">${new Date().toLocaleString()}</div>`;
  listEl.appendChild(li);
  // keep scroll at bottom so list grows visibly
  listEl.scrollTop = listEl.scrollHeight;
}

