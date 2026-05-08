/* ═══════════════════════════════════════════════════════════════
   Jarvis Remote — Dashboard Controller v2 (app.js)
   Tab-based mobile-first UI with real-time streaming,
   proper volume/mute control, and Jarvis terminal IPC.
   ═══════════════════════════════════════════════════════════════ */

const $ = id => document.getElementById(id);

// ─── State ─────────────────────────────────────────────────────
let streamActive = true;
let streamWs = null;
let mjpegActive = false;
let isMuted = false;
let currentVolume = 50;
let volumeThrottleTimer = null;
let fpsCounter = { frames: 0, last: performance.now(), value: 0 };
let systemPollTimer = null;
let terminalPollTimer = null;
let reconnectAttempts = 0;
const MAX_RECONNECT = 10;
let isEmergencyLocked = false;
let emergencyPassword = null;
let terminalLastCount = 0;
let activeTab = 'tab-home';

// ─── Initialization ────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    fetchVolume();
    fetchSystemStatus();
    fetchAutostartStatus();
    fetchSecurityStatus();
    loadTerminalHistory();

    systemPollTimer = setInterval(fetchSystemStatus, 5000);
    setInterval(fetchSecurityStatus, 5000);
    terminalPollTimer = setInterval(pollTerminalUpdates, 2000);

    // Touch optimizations for volume slider
    const slider = $('vol-slider');
    if (slider) {
        slider.addEventListener('touchstart', e => e.stopPropagation(), { passive: true });
        slider.addEventListener('touchmove', e => e.stopPropagation(), { passive: true });
    }

    updateConnectionStatus(true);
});

// ═══════════════════════════════════════════════════════════════
//  TAB NAVIGATION
// ═══════════════════════════════════════════════════════════════

function switchTab(btn) {
    if (!btn) return;
    const tabId = btn.getAttribute('data-tab');
    if (!tabId) return;

    // Update nav buttons
    document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');

    // Update content
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    const target = $(tabId);
    if (target) target.classList.add('active');

    // Start/stop stream based on tab
    if (tabId === 'tab-screen') {
        if (!streamActive || (!streamWs && !mjpegActive)) {
            streamActive = true;
            reconnectAttempts = 0;
            initStream();
        }
    }

    // Focus terminal input
    if (tabId === 'tab-terminal') {
        setTimeout(() => {
            const input = $('terminal-input');
            if (input) input.focus();
        }, 300);
    }

    activeTab = tabId;
}

// ═══════════════════════════════════════════════════════════════
//  SCREEN STREAMING
// ═══════════════════════════════════════════════════════════════

function initStream() {
    if (!streamActive) return;
    tryWebSocketStream();
}

function tryWebSocketStream() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/api/screen/ws`;

    try {
        streamWs = new WebSocket(url);
        streamWs.binaryType = 'arraybuffer';

        streamWs.onopen = () => {
            reconnectAttempts = 0;
            showStreamActive();
        };

        streamWs.onmessage = (event) => {
            if (!streamActive) return;
            const blob = new Blob([event.data], { type: 'image/jpeg' });
            const url = URL.createObjectURL(blob);
            const img = $('stream-img');
            const oldSrc = img.src;
            img.onload = () => {
                URL.revokeObjectURL(oldSrc);
                hidePlaceholder();
            };
            img.src = url;
            countFrame();
        };

        streamWs.onerror = () => {
            streamWs.close();
        };

        streamWs.onclose = () => {
            streamWs = null;
            if (streamActive && !mjpegActive) {
                if (reconnectAttempts < MAX_RECONNECT) {
                    reconnectAttempts++;
                    const delay = Math.min(2000 * reconnectAttempts, 10000);
                    setTimeout(() => {
                        if (streamActive) tryWebSocketStream();
                    }, delay);
                } else {
                    startMjpegFallback();
                }
            }
        };
    } catch {
        startMjpegFallback();
    }
}

function startMjpegFallback() {
    mjpegActive = true;
    const img = $('stream-img');
    img.src = `/api/screen/stream?t=${Date.now()}`;
    img.onload = () => { hidePlaceholder(); showStreamActive(); };
    img.onerror = () => { showStreamError(); };
}

function toggleStream() {
    streamActive = !streamActive;
    const icon = $('stream-toggle-icon');
    const badge = $('stream-status');

    if (streamActive) {
        icon.textContent = '⏸';
        badge.textContent = 'CANLI';
        badge.style.cssText = 'background:rgba(0,255,136,0.1);color:var(--green);border-color:rgba(0,255,136,0.2)';
        reconnectAttempts = 0;
        mjpegActive = false;
        initStream();
    } else {
        icon.textContent = '▶️';
        badge.textContent = 'DURDURULDU';
        badge.style.cssText = 'background:rgba(255,204,0,0.1);color:var(--yellow);border-color:rgba(255,204,0,0.2)';
        if (streamWs) { streamWs.close(); streamWs = null; }
        if (mjpegActive) { $('stream-img').src = ''; mjpegActive = false; }
    }
}

function showStreamActive() {
    const badge = $('stream-status');
    if (badge && streamActive) {
        badge.textContent = 'CANLI';
        badge.style.cssText = 'background:rgba(0,255,136,0.1);color:var(--green);border-color:rgba(0,255,136,0.2)';
    }
}

function showStreamError() {
    const badge = $('stream-status');
    if (badge) {
        badge.textContent = 'HATA';
        badge.style.cssText = 'background:rgba(255,51,85,0.1);color:var(--red);border-color:rgba(255,51,85,0.2)';
    }
}

function hidePlaceholder() {
    const ph = $('stream-placeholder');
    if (ph) ph.style.display = 'none';
}

function countFrame() {
    fpsCounter.frames++;
    const now = performance.now();
    const delta = now - fpsCounter.last;
    if (delta >= 1000) {
        fpsCounter.value = Math.round(fpsCounter.frames * 1000 / delta);
        fpsCounter.frames = 0;
        fpsCounter.last = now;
        const label = $('stream-fps-label');
        if (label) label.textContent = `${fpsCounter.value} FPS`;
    }
}

// ═══════════════════════════════════════════════════════════════
//  VOLUME CONTROL
// ═══════════════════════════════════════════════════════════════

async function fetchVolume() {
    try {
        const resp = await fetch('/api/audio/volume');
        if (!resp.ok) return;
        const data = await resp.json();
        currentVolume = data.volume;
        isMuted = data.muted;
        updateVolumeUI(currentVolume, isMuted);
    } catch (err) {
        console.error('[Volume] Fetch error:', err);
    }
}

function onVolumeSlide(val) {
    val = parseInt(val);
    currentVolume = val;
    updateVolumeUI(val, false);
}

function onVolumeSet(val) {
    val = parseInt(val);
    currentVolume = val;

    if (isMuted) {
        isMuted = false;
        fetch('/api/audio/mute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mute: false }),
        }).catch(() => {});
    }

    if (volumeThrottleTimer) clearTimeout(volumeThrottleTimer);
    volumeThrottleTimer = setTimeout(() => {
        sendVolumeToServer(val);
    }, 100);
}

async function sendVolumeToServer(level) {
    try {
        await fetch('/api/audio/volume', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ level }),
        });
    } catch (err) {
        showToast('Ses ayarlanamadı', 'error');
    }
}

async function toggleMute() {
    const newMuted = !isMuted;
    try {
        const resp = await fetch('/api/audio/mute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mute: newMuted }),
        });
        const data = await resp.json();
        if (data.success) {
            isMuted = newMuted;
            updateVolumeUI(currentVolume, isMuted);
        }
    } catch {
        showToast('Sessiz mod değiştirilemedi', 'error');
    }
}

function setVolumeQuick(level) {
    currentVolume = level;
    if (isMuted) {
        isMuted = false;
        fetch('/api/audio/mute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mute: false }),
        }).catch(() => {});
    }
    updateVolumeUI(level, false);
    const slider = $('vol-slider');
    if (slider) slider.value = level;
    sendVolumeToServer(level);
}

function updateVolumeUI(volume, muted) {
    const display = $('vol-display');
    const slider = $('vol-slider');
    const muteBtn = $('btn-mute');

    if (display) {
        display.textContent = muted ? '🔇' : volume;
        display.classList.toggle('muted', muted);
    }
    if (slider) {
        slider.value = volume;
        slider.style.setProperty('--fill', volume + '%');
    }
    if (muteBtn) {
        if (muted) {
            muteBtn.classList.add('muted');
            muteBtn.innerHTML = '🔊 Sesi Aç';
        } else {
            muteBtn.classList.remove('muted');
            muteBtn.innerHTML = '🔇 Sessiz';
        }
    }

    // Update stat ring
    const statVol = $('stat-vol');
    if (statVol) statVol.textContent = muted ? 'SESİZ' : volume + '%';

    // Update quick mute button on home
    const qIcon = $('quick-mute-icon');
    const qText = $('quick-mute-text');
    if (qIcon) qIcon.textContent = muted ? '🔊' : '🔇';
    if (qText) qText.textContent = muted ? 'Sesi Aç' : 'Sessiz';

    // Update ring
    const ringFill = $('ring-vol-fill');
    if (ringFill) ringFill.setAttribute('stroke-dasharray', `${volume}, 100`);
}

// ═══════════════════════════════════════════════════════════════
//  SYSTEM STATUS
// ═══════════════════════════════════════════════════════════════

async function fetchSystemStatus() {
    try {
        const resp = await fetch('/api/system/status');
        if (!resp.ok) { updateConnectionStatus(false); return; }
        const data = await resp.json();
        updateConnectionStatus(true);

        // CPU ring
        const cpu = Math.round(data.cpu);
        const statCpu = $('stat-cpu');
        const ringCpu = $('ring-cpu-fill');
        if (statCpu) statCpu.textContent = cpu + '%';
        if (ringCpu) {
            ringCpu.setAttribute('stroke-dasharray', `${cpu}, 100`);
            ringCpu.classList.toggle('warn', cpu > 60 && cpu <= 80);
            ringCpu.classList.toggle('danger', cpu > 80);
        }

        // Memory ring
        const mem = Math.round(data.memory);
        const statMem = $('stat-mem');
        const ringMem = $('ring-mem-fill');
        if (statMem) statMem.textContent = mem + '%';
        if (ringMem) {
            ringMem.setAttribute('stroke-dasharray', `${mem}, 100`);
            ringMem.classList.toggle('warn', mem > 70 && mem <= 85);
            ringMem.classList.toggle('danger', mem > 85);
        }

        // Uptime
        const uptimeBadge = $('uptime-badge');
        if (uptimeBadge) uptimeBadge.textContent = data.uptime || '--';

        // Tunnel
        const statTunnel = $('stat-tunnel');
        if (statTunnel) {
            if (data.tunnel_url) {
                const short = data.tunnel_url.replace('https://', '').split('.')[0];
                statTunnel.textContent = short + '...';
                statTunnel.title = data.tunnel_url;
            } else {
                statTunnel.textContent = 'Bekleniyor…';
            }
        }
    } catch {
        updateConnectionStatus(false);
    }
}

function updateConnectionStatus(connected) {
    const dot = $('conn-dot');
    const text = $('conn-text');
    if (dot && text) {
        if (connected) {
            dot.style.background = 'var(--green)';
            text.textContent = 'BAĞLI';
            text.style.color = 'var(--green)';
        } else {
            dot.style.background = 'var(--red)';
            text.textContent = 'BAĞLANTI YOK';
            text.style.color = 'var(--red)';
        }
    }
}

// ═══════════════════════════════════════════════════════════════
//  TERMINAL (Jarvis Chat)
// ═══════════════════════════════════════════════════════════════

async function loadTerminalHistory() {
    try {
        const resp = await fetch('/api/terminal/history');
        if (!resp.ok) return;
        const data = await resp.json();
        const container = $('terminal-history');
        if (!container || !data.history || data.history.length === 0) return;

        // Keep welcome message, add history after
        terminalLastCount = 0;
        data.history.forEach(msg => {
            appendChatMessage(msg);
            terminalLastCount++;
        });
        scrollTerminalToBottom();
    } catch {}
}

async function sendTerminalMessage() {
    const input = $('terminal-input');
    const btn = $('btn-send-msg');
    if (!input || !btn) return;

    const message = input.value.trim();
    if (!message) return;

    input.value = '';
    btn.disabled = true;
    input.disabled = true;

    appendChatMessage({ role: 'user', text: message, time: new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' }) });
    terminalLastCount++;
    scrollTerminalToBottom();

    const typingId = showTypingIndicator();

    try {
        const resp = await fetch('/api/terminal/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message }),
        });
        const data = await resp.json();

        removeTypingIndicator(typingId);

        if (data.status === 'answered' && data.response) {
            appendChatMessage({
                role: 'jarvis',
                text: data.response,
                time: new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' })
            });
            terminalLastCount++;
        } else if (data.status === 'pending') {
            appendChatMessage({
                role: 'jarvis',
                text: '⏳ İşleniyor... Yanıt birazdan gelecek.',
                time: new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' }),
                pending: true
            });
            terminalLastCount++;
        }
        scrollTerminalToBottom();
    } catch {
        removeTypingIndicator(typingId);
        appendChatMessage({ role: 'jarvis', text: '⚠️ Bağlantı hatası.', time: '' });
        terminalLastCount++;
        scrollTerminalToBottom();
    } finally {
        btn.disabled = false;
        input.disabled = false;
        input.focus();
    }
}

function showTypingIndicator() {
    const container = $('terminal-history');
    if (!container) return null;
    const div = document.createElement('div');
    div.className = 'chat-msg chat-jarvis chat-typing';
    div.id = 'typing-indicator';
    div.innerHTML = `
        <div class="typing-dots">
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
        </div>
        <div class="chat-time">Jarvis düşünüyor…</div>
    `;
    container.appendChild(div);
    scrollTerminalToBottom();
    return 'typing-indicator';
}

function removeTypingIndicator(id) {
    if (!id) return;
    const el = document.getElementById(id);
    if (el) el.remove();
}

async function pollTerminalUpdates() {
    try {
        const resp = await fetch('/api/terminal/history');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.history) return;

        if (data.history.length > terminalLastCount) {
            const container = $('terminal-history');
            if (!container) return;

            // Remove pending messages
            container.querySelectorAll('[data-pending="true"]').forEach(el => el.remove());

            const newMsgs = data.history.slice(terminalLastCount);
            newMsgs.forEach(msg => appendChatMessage(msg));
            terminalLastCount = data.history.length;
            scrollTerminalToBottom();
        }
    } catch {}
}

function appendChatMessage(msg) {
    const container = $('terminal-history');
    if (!container) return;

    const div = document.createElement('div');
    const isUser = msg.role === 'user';
    div.className = `chat-msg ${isUser ? 'chat-user' : 'chat-jarvis'}`;
    if (msg.pending) div.setAttribute('data-pending', 'true');

    const timeStr = msg.time || (msg.timestamp ? new Date(msg.timestamp * 1000).toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' }) : '');

    div.innerHTML = `
        <div class="chat-sender">${isUser ? '👤 Sen' : '🤖 Jarvis'}</div>
        <div class="chat-text">${escapeHtml(msg.text || '')}</div>
        ${timeStr ? `<div class="chat-time">${timeStr}</div>` : ''}
    `;
    container.appendChild(div);
}

async function clearTerminal() {
    try {
        await fetch('/api/terminal/clear', { method: 'POST' });
        const container = $('terminal-history');
        if (container) {
            container.innerHTML = `
                <div class="chat-msg chat-jarvis chat-welcome">
                    <div class="chat-sender">🤖 Jarvis</div>
                    <div class="chat-text">Terminal temizlendi. Size nasıl yardımcı olabilirim?</div>
                </div>
            `;
        }
        terminalLastCount = 0;
        showToast('Terminal temizlendi', 'success');
    } catch {
        showToast('Terminal temizlenemedi', 'error');
    }
}

function scrollTerminalToBottom() {
    const container = $('terminal-history');
    if (container) {
        requestAnimationFrame(() => {
            container.scrollTop = container.scrollHeight;
        });
    }
}

// ═══════════════════════════════════════════════════════════════
//  SYSTEM CONTROLS
// ═══════════════════════════════════════════════════════════════

async function launchDesktopUI() {
    const btn = $('btn-launch-ui');
    if (btn) btn.disabled = true;

    try {
        showToast('Masaüstü arayüzü başlatılıyor…', 'info');
        const resp = await fetch('/api/ui/launch', { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            showToast('Masaüstü arayüzü başlatıldı!', 'success');
        } else {
            showToast(data.message || 'Başlatma başarısız.', 'error');
        }
    } catch {
        showToast('Bağlantı hatası.', 'error');
    } finally {
        if (btn) setTimeout(() => btn.disabled = false, 3000);
    }
}

async function installAutostart() {
    try {
        const resp = await fetch('/api/autostart/install', { method: 'POST' });
        const data = await resp.json();
        showToast(data.success ? 'Otomatik başlatma kuruldu!' : 'Kurulum başarısız.', data.success ? 'success' : 'error');
        fetchAutostartStatus();
    } catch {
        showToast('Bağlantı hatası.', 'error');
    }
}

async function uninstallAutostart() {
    try {
        const resp = await fetch('/api/autostart/uninstall', { method: 'POST' });
        const data = await resp.json();
        showToast(data.success ? 'Otomatik başlatma kaldırıldı.' : 'Kaldırma başarısız.', data.success ? 'success' : 'error');
        fetchAutostartStatus();
    } catch {
        showToast('Bağlantı hatası.', 'error');
    }
}

async function fetchAutostartStatus() {
    try {
        const resp = await fetch('/api/autostart/status');
        const data = await resp.json();
        const el = $('autostart-status');
        if (el) {
            el.innerHTML = data.installed
                ? '<span style="color:var(--green);">✅ Otomatik başlatma aktif</span>'
                : '<span style="color:var(--text-dim);">⭕ Otomatik başlatma devre dışı</span>';
        }
    } catch {}
}

// ═══════════════════════════════════════════════════════════════
//  EMERGENCY LOCK
// ═══════════════════════════════════════════════════════════════

async function fetchSecurityStatus() {
    try {
        const resp = await fetch('/api/security/status');
        if (!resp.ok) return;
        const data = await resp.json();
        isEmergencyLocked = data.locked;
        emergencyPassword = data.password;
        updateEmergencyLockUI();
    } catch {}
}

async function toggleEmergencyLock() {
    const btn = $('btn-emergency-lock');
    if (!btn) return;
    btn.disabled = true;

    try {
        if (isEmergencyLocked) {
            const resp = await fetch('/api/security/unlock', { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                showToast('Kilit açıldı!', 'success');
                isEmergencyLocked = false;
                emergencyPassword = null;
            } else {
                showToast(data.message || 'Kilit açılamadı.', 'error');
            }
        } else {
            if (!confirm('Bilgisayar kilitlenecek. Onaylıyor musunuz?')) {
                btn.disabled = false;
                return;
            }
            const resp = await fetch('/api/security/lock', { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                showToast('Bilgisayar kilitlendi!', 'success');
                isEmergencyLocked = true;
                emergencyPassword = data.password;
            }
        }
        updateEmergencyLockUI();
    } catch {
        showToast('Bağlantı hatası.', 'error');
    } finally {
        btn.disabled = false;
    }
}

function updateEmergencyLockUI() {
    const btn = $('btn-emergency-lock');
    const displayBox = $('emergency-password-display');
    const passText = $('emergency-password');
    if (!btn || !displayBox || !passText) return;

    if (isEmergencyLocked) {
        btn.innerHTML = '🔓 KİLİDİ AÇ';
        btn.className = 'btn btn-primary';
        displayBox.style.display = 'block';
        passText.textContent = emergencyPassword || 'Bilinmiyor';
    } else {
        btn.innerHTML = '🚨 ACİL KİLİT';
        btn.className = 'btn btn-danger';
        displayBox.style.display = 'none';
        passText.textContent = '---';
    }
}

// ═══════════════════════════════════════════════════════════════
//  UTILITIES
// ═══════════════════════════════════════════════════════════════

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showToast(message, type = 'info') {
    const existing = document.querySelector('.toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);

    requestAnimationFrame(() => toast.classList.add('show'));
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}
