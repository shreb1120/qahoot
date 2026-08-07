const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const tagPrompt = document.getElementById('tagPrompt');
const tagPromptFile = document.getElementById('tagPromptFile');
const tagPromptError = document.getElementById('tagPromptError');
const alvTagInput = document.getElementById('alvTagInput');
const agentNameInput = document.getElementById('agentNameInput');
const progressArea = document.getElementById('progressArea');
const resultsArea = document.getElementById('resultsArea');
const errorBanner = document.getElementById('errorBanner');
const historyArea = document.getElementById('historyArea');
const historyList = document.getElementById('historyList');
const writeupBtn = document.getElementById('writeupBtn');
const verbalBtn = document.getElementById('verbalBtn');
const writeupModalTitle = document.getElementById('writeupModalTitle');
const writeupModal = document.getElementById('writeupModal');
const writeupAgentInput = document.getElementById('writeupAgentInput');
const writeupError = document.getElementById('writeupError');
const writeupGenerateBtn = document.getElementById('writeupGenerateBtn');

let pollTimer = null;
let pendingFile = null;
let currentAnalysisId = null;
let currentAgentName = '';
let baseResults = null;        // Untouched AI results for current view
let currentOverrides = { approval: {}, post_enrollment: {}, determination: null };
let aiOriginalDetermination = '';
let saveTimer = null;
let historyItems = [];          // Last fetched history list, for client-side filtering

const ALV_TAG_RE = /^ALV-\S.*$/i;

const INELIGIBLE_REASONS = {
    already_settled: 'Already settled through another company',
    vehicle_secured: 'Secured to a vehicle',
    already_sued: 'Already sued on this account',
};

dropZone.addEventListener('click', () => fileInput.click());

dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('dragover');
});

dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('dragover');
});

dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer.files.length > 0) {
        promptForTag(e.dataTransfer.files[0]);
    }
});

fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) {
        promptForTag(fileInput.files[0]);
    }
});

document.getElementById('toggleHistoryBtn').addEventListener('click', showHistory);
document.getElementById('backToUploadBtn').addEventListener('click', backToUpload);
document.getElementById('flipFilterToggle').addEventListener('change', () => renderHistoryList(historyItems));
document.getElementById('historySearch').addEventListener('input', () => renderHistoryList(historyItems));
document.getElementById('newAnalysisBtn').addEventListener('click', newAnalysis);
document.getElementById('downloadReportBtn').addEventListener('click', () => downloadReport(false));
document.getElementById('downloadFailsBtn').addEventListener('click', () => downloadReport(true));
document.getElementById('tagAnalyzeBtn').addEventListener('click', confirmTagAndUpload);
document.getElementById('tagCancelBtn').addEventListener('click', cancelTagPrompt);
writeupBtn.addEventListener('click', () => openWriteupModal('written'));
verbalBtn.addEventListener('click', () => openWriteupModal('verbal'));
document.getElementById('writeupCancelBtn').addEventListener('click', closeWriteupModal);
writeupGenerateBtn.addEventListener('click', generateWriteup);
writeupAgentInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); generateWriteup(); }
    else if (e.key === 'Escape') { closeWriteupModal(); }
});
[alvTagInput, agentNameInput].forEach(el => {
    el.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            confirmTagAndUpload();
        } else if (e.key === 'Escape') {
            cancelTagPrompt();
        }
    });
});

function promptForTag(file) {
    hideError();
    pendingFile = file;
    tagPromptFile.textContent = file.name;
    tagPromptError.textContent = '';
    alvTagInput.value = 'ALV-';
    agentNameInput.value = '';
    dropZone.style.display = 'none';
    historyArea.style.display = 'none';
    resultsArea.style.display = 'none';
    progressArea.style.display = 'none';
    tagPrompt.style.display = 'block';
    // Place cursor at end so user can just keep typing after the prefix.
    setTimeout(() => {
        alvTagInput.focus();
        const len = alvTagInput.value.length;
        alvTagInput.setSelectionRange(len, len);
    }, 0);
}

function cancelTagPrompt() {
    pendingFile = null;
    tagPrompt.style.display = 'none';
    fileInput.value = '';
    dropZone.style.display = 'block';
}

function confirmTagAndUpload() {
    const rawTag = (alvTagInput.value || '').trim();
    const rawAgent = (agentNameInput.value || '').trim().replace(/\s+/g, ' ');
    if (!ALV_TAG_RE.test(rawTag)) {
        tagPromptError.textContent = 'Tag must start with "ALV-" and include an identifier (e.g., ALV-12345).';
        alvTagInput.focus();
        return;
    }
    if (!rawAgent) {
        tagPromptError.textContent = 'Agent name is required.';
        agentNameInput.focus();
        return;
    }
    const tag = 'ALV-' + rawTag.slice(4);
    tagPrompt.style.display = 'none';
    if (!pendingFile) return;
    const file = pendingFile;
    pendingFile = null;
    uploadFile(file, tag, rawAgent);
}

function checkAuth(resp) {
    if (resp.status === 401) {
        window.location = '/login';
        throw new Error('Session expired');
    }
    return resp;
}

async function uploadFile(file, alvTag, agentName) {
    hideError();
    historyArea.style.display = 'none';
    resultsArea.style.display = 'none';
    progressArea.style.display = 'block';
    dropZone.style.display = 'none';

    document.getElementById('fileName').textContent = file.name;
    updateProgress(10, 'Uploading file...');

    const formData = new FormData();
    formData.append('audio', file);
    formData.append('alv_tag', alvTag);
    formData.append('agent_name', agentName);

    try {
        const resp = await fetch('/analyze', {
            method: 'POST',
            body: formData,
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        checkAuth(resp);
        const data = await resp.json();

        if (!resp.ok) {
            throw new Error(data.error || 'Upload failed');
        }

        updateProgress(20, 'Upload complete. Starting transcription...');
        pollStatus(data.job_id);
    } catch (err) {
        showError(err.message);
        resetUI();
    }
}

function pollStatus(jobId) {
    pollTimer = setInterval(async () => {
        try {
            const resp = await fetch(`/status/${jobId}`, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });
            checkAuth(resp);
            const data = await resp.json();

            if (data.status === 'transcribing') {
                updateProgress(40, 'Transcribing audio with speaker detection...');
            } else if (data.status === 'analyzing') {
                updateProgress(75, 'Analyzing transcript against QA checklist...');
            } else if (data.status === 'complete') {
                clearInterval(pollTimer);
                updateProgress(100, 'Complete!');
                const meta = {
                    alv_tag: data.alv_tag,
                    agent_name: data.agent_name,
                    call_date: data.call_date,
                    client_phone: data.client_phone,
                    filename: data.filename,
                    analysis_id: data.analysis_id,
                };
                setTimeout(() => renderResults(data.results, data.transcript, meta), 500);
            } else if (data.status === 'error') {
                clearInterval(pollTimer);
                showError(data.error || 'An error occurred during processing.');
                resetUI();
            }
        } catch {
            clearInterval(pollTimer);
            showError('Lost connection to server.');
            resetUI();
        }
    }, 2000);
}

function updateProgress(pct, msg) {
    document.getElementById('progressFill').style.width = pct + '%';
    document.getElementById('statusText').innerHTML =
        (pct < 100 ? '<span class="spinner"></span>' : '') + msg;
}

function renderResults(results, transcript, meta) {
    progressArea.style.display = 'none';
    historyArea.style.display = 'none';
    resultsArea.style.display = 'block';

    baseResults = deepClone(results);
    currentAnalysisId = (meta && meta.analysis_id) || null;
    currentAgentName = (meta && meta.agent_name) || '';
    currentOverrides = sanitizeIncomingOverrides(meta && meta.overrides);
    aiOriginalDetermination = ((results.final_determination || {}).result || '').toUpperCase();

    writeupBtn.disabled = !currentAnalysisId;
    verbalBtn.disabled = !currentAnalysisId;

    renderProgramFlip(results.program_flip);
    renderIneligibleAccounts(results.ineligible_accounts);
    renderCollectionsContext(results.collections_context);
    renderCallMeta(meta);
    document.getElementById('summaryText').textContent = results.summary || '';

    if (transcript) {
        document.getElementById('transcriptBody').textContent = transcript;
    }

    rerenderFromOverrides();
    setupAccordions();
}

function deepClone(o) { return JSON.parse(JSON.stringify(o)); }

function sanitizeIncomingOverrides(o) {
    const empty = { approval: {}, post_enrollment: {}, determination: null };
    if (!o || typeof o !== 'object') return empty;
    return {
        approval: (o.approval && typeof o.approval === 'object') ? o.approval : {},
        post_enrollment: (o.post_enrollment && typeof o.post_enrollment === 'object') ? o.post_enrollment : {},
        determination: o.determination || null,
    };
}

function applyOverridesToResults(results, overrides) {
    if (!results || !overrides) return results;
    const out = deepClone(results);
    [['approval_script', 'approval'], ['post_enrollment_script', 'post_enrollment']].forEach(([rk, ok]) => {
        const section = out[rk];
        if (!section || !Array.isArray(section.items)) return;
        const sectionOv = overrides[ok] || {};
        Object.entries(sectionOv).forEach(([idxStr, newStatus]) => {
            const idx = parseInt(idxStr, 10);
            if (!Number.isFinite(idx) || idx < 0 || idx >= section.items.length) return;
            if (section.items[idx].status !== newStatus) {
                section.items[idx] = { ...section.items[idx], status: newStatus, overridden: true };
            }
        });
        section.covered_count = section.items.filter(i => i.status === 'covered').length;
    });
    if (overrides.determination) {
        out.final_determination = { ...(out.final_determination || {}), result: overrides.determination, overridden: true };
    }
    return out;
}

function rerenderFromOverrides() {
    const adjusted = applyOverridesToResults(baseResults, currentOverrides);

    const det = adjusted.final_determination || {};
    const banner = document.getElementById('determination');
    const result = (det.result || 'UNKNOWN').toUpperCase();
    banner.textContent = result + (det.overridden ? '  (overridden)' : '');
    banner.className = 'determination-banner';
    if (result.startsWith('PASS')) banner.classList.add('pass');
    else if (result.includes('CRITICAL')) banner.classList.add('critical');
    else banner.classList.add('fail');

    document.querySelectorAll('#determinationOverride .det-btn').forEach(btn => {
        const target = btn.getAttribute('data-det') || '';
        const active = (currentOverrides.determination || '') === target;
        btn.classList.toggle('active', active);
    });

    renderScriptSection('approvalItems', 'approvalScore', adjusted.approval_script, 'approval');
    renderScriptSection('enrollmentItems', 'enrollmentScore', adjusted.post_enrollment_script, 'post_enrollment');
    renderHighRisk(adjusted.high_risk_phrases);
}

function persistOverridesDebounced() {
    if (!currentAnalysisId) return;
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(persistOverrides, 350);
}

async function persistOverrides() {
    if (!currentAnalysisId) return;
    try {
        await fetch(`/analysis/${currentAnalysisId}/override`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
            body: JSON.stringify(currentOverrides),
        });
    } catch (e) {
        showError('Could not save override. Changes are local until saved.');
    }
}

function toggleItemOverride(sectionKey, idx, newStatus) {
    if (!currentOverrides[sectionKey]) currentOverrides[sectionKey] = {};
    const baseSection = baseResults[sectionKey === 'approval' ? 'approval_script' : 'post_enrollment_script'];
    const original = baseSection?.items?.[idx]?.status;
    if (newStatus === original) {
        delete currentOverrides[sectionKey][String(idx)];
    } else {
        currentOverrides[sectionKey][String(idx)] = newStatus;
    }
    rerenderFromOverrides();
    persistOverridesDebounced();
}

document.querySelectorAll('#determinationOverride .det-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const target = btn.getAttribute('data-det') || '';
        currentOverrides.determination = target || null;
        rerenderFromOverrides();
        persistOverridesDebounced();
    });
});

function renderProgramFlip(flip) {
    const banner = document.getElementById('programFlipBanner');
    if (!banner) return;
    if (flip && flip.detected) {
        const note = flip.evidence
            ? `<span class="program-flip-note">${esc(flip.evidence)}</span>`
            : '';
        banner.innerHTML =
            `<span class="program-flip-title">&#9888; Possible program flip &mdash; review manually</span>${note}`;
        banner.style.display = 'block';
    } else {
        banner.innerHTML = '';
        banner.style.display = 'none';
    }
}

function renderIneligibleAccounts(data) {
    const banner = document.getElementById('ineligibleBanner');
    if (!banner) return;
    const items = (data && data.detected && Array.isArray(data.items))
        ? data.items.filter(x => x && (x.reason || x.evidence))
        : [];
    if (!items.length) {
        banner.innerHTML = '';
        banner.style.display = 'none';
        return;
    }
    const rows = items.map(it => {
        const label = INELIGIBLE_REASONS[it.reason] || 'Possible ineligible account';
        const ts = it.timestamp ? ` <span class="ineligible-ts">${esc(it.timestamp)}</span>` : '';
        const ev = it.evidence ? `<span class="ineligible-ev">${esc(it.evidence)}</span>` : '';
        return `<li><span class="ineligible-reason">${esc(label)}</span>${ts}${ev}</li>`;
    }).join('');
    banner.innerHTML =
        `<div class="ineligible-title">&#9940; Account(s) that may not be eligible to enroll &mdash; review manually</div>` +
        `<ul class="ineligible-list">${rows}</ul>`;
    banner.style.display = 'block';
}

function renderCollectionsContext(data) {
    const banner = document.getElementById('collectionsBanner');
    if (!banner) return;
    if (data && data.detected) {
        const note = data.evidence
            ? `<span class="collections-note">${esc(data.evidence)}</span>`
            : '';
        banner.innerHTML =
            `<span class="collections-title">&#9873; Accounts appear to be in collections &mdash; ` +
            `missed-payment / credit-impact disclosures may not apply; review manually</span>${note}`;
        banner.style.display = 'block';
    } else {
        banner.innerHTML = '';
        banner.style.display = 'none';
    }
}

function renderCallMeta(meta) {
    const container = document.getElementById('callMeta');
    if (!container) return;
    container.innerHTML = '';
    if (!meta) { container.style.display = 'none'; return; }
    const fields = [
        { label: 'ALV Tag', value: meta.alv_tag },
        { label: 'Agent', value: meta.agent_name },
        { label: 'Call Date', value: meta.call_date },
        { label: 'Client Phone', value: meta.client_phone },
        { label: 'File', value: meta.filename },
    ].filter(f => f.value);
    if (fields.length === 0) { container.style.display = 'none'; return; }
    container.style.display = 'flex';
    fields.forEach(f => {
        const cell = document.createElement('div');
        cell.className = 'call-meta-cell';
        cell.innerHTML = `<span class="call-meta-label">${esc(f.label)}</span><span class="call-meta-value">${esc(f.value)}</span>`;
        container.appendChild(cell);
    });
}

function renderScriptSection(containerId, scoreId, data, sectionKey) {
    const container = document.getElementById(containerId);
    const scoreEl = document.getElementById(scoreId);
    container.innerHTML = '';

    if (!data || !data.items) {
        container.innerHTML = '<p class="no-issues">No data available</p>';
        return;
    }

    const covered = data.covered_count || 0;
    const total = data.total || data.items.length;
    scoreEl.textContent = `${covered} / ${total}`;

    scoreEl.className = 'score';
    if (covered === total) scoreEl.classList.add('perfect');
    else if (covered >= total * 0.7) scoreEl.classList.add('partial');
    else scoreEl.classList.add('bad');

    data.items.forEach((item, idx) => {
        const isCovered = item.status === 'covered';
        const row = document.createElement('div');
        row.className = 'item-row' + (item.overridden ? ' overridden' : '');
        row.innerHTML = `
            <div class="item-status ${isCovered ? 'covered' : 'not-covered'}">
                ${isCovered ? '&#10003;' : '&#10007;'}
            </div>
            <div class="item-details">
                <div class="item-name">${esc(item.name)}${item.overridden ? ' <span class="overridden-tag">overridden</span>' : ''}</div>
                <div class="item-evidence">${esc(item.evidence || '')}</div>
            </div>
            <div class="item-timestamp">${esc(item.timestamp || '')}</div>
            <label class="item-toggle" title="Toggle covered / not covered">
                <input type="checkbox" ${isCovered ? 'checked' : ''} data-section="${sectionKey}" data-idx="${idx}">
                <span class="item-toggle-slider"></span>
            </label>
        `;
        const cb = row.querySelector('input[type="checkbox"]');
        cb.addEventListener('change', () => {
            toggleItemOverride(sectionKey, idx, cb.checked ? 'covered' : 'not_covered');
        });
        container.appendChild(row);
    });
}

function renderHighRisk(data) {
    const container = document.getElementById('highRiskItems');
    const scoreEl = document.getElementById('highRiskScore');
    container.innerHTML = '';

    if (!data || !data.detected || !data.phrases || data.phrases.length === 0) {
        scoreEl.textContent = 'None';
        scoreEl.className = 'score perfect';
        container.innerHTML = '<p class="no-issues">No high-risk phrases detected</p>';
        return;
    }

    scoreEl.textContent = `${data.phrases.length} found`;
    scoreEl.className = 'score bad';

    data.phrases.forEach(p => {
        const div = document.createElement('div');
        div.className = 'high-risk-item';
        div.innerHTML = `
            <div class="phrase">${esc(p.phrase)} <span class="item-timestamp">${esc(p.timestamp || '')}</span></div>
            <div class="quote">"${esc(p.quote || '')}"</div>
            <div class="violation-note">${esc(p.violation || '')}</div>
        `;
        container.appendChild(div);
    });
}

function setupAccordions() {
    document.querySelectorAll('.section-header').forEach(header => {
        header.onclick = () => header.parentElement.classList.toggle('open');
    });
}

function showError(msg) {
    errorBanner.textContent = msg;
    errorBanner.style.display = 'block';
}

function hideError() {
    errorBanner.style.display = 'none';
}

function resetUI() {
    progressArea.style.display = 'none';
    tagPrompt.style.display = 'none';
    dropZone.style.display = 'block';
    fileInput.value = '';
    pendingFile = null;
}

function newAnalysis() {
    resultsArea.style.display = 'none';
    historyArea.style.display = 'none';
    tagPrompt.style.display = 'none';
    dropZone.style.display = 'block';
    fileInput.value = '';
    pendingFile = null;
    hideError();
}

async function showHistory() {
    hideError();
    dropZone.style.display = 'none';
    progressArea.style.display = 'none';
    resultsArea.style.display = 'none';
    historyArea.style.display = 'block';
    const searchEl = document.getElementById('historySearch');
    if (searchEl) searchEl.value = '';
    historyList.innerHTML = '<p class="muted">Loading...</p>';

    try {
        const resp = await fetch('/history', {
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        checkAuth(resp);
        if (!resp.ok) throw new Error('Could not load history');
        const items = await resp.json();
        historyItems = items;
        renderHistoryList(historyItems);
    } catch (err) {
        historyList.innerHTML = `<p class="error-inline">${esc(err.message)}</p>`;
    }
}

function backToUpload() {
    historyArea.style.display = 'none';
    resultsArea.style.display = 'none';
    progressArea.style.display = 'none';
    tagPrompt.style.display = 'none';
    dropZone.style.display = 'block';
    pendingFile = null;
    hideError();
}

function renderHistoryList(items) {
    items = items || [];
    const flipOnly = document.getElementById('flipFilterToggle')?.checked;
    let view = flipOnly ? items.filter(i => i.program_flip === 1) : items;

    const q = (document.getElementById('historySearch')?.value || '').trim().toLowerCase();
    if (q) {
        const digitsQ = q.replace(/\D/g, '');
        view = view.filter(i => {
            const alv = (i.alv_tag || '').toLowerCase();
            const agent = (i.agent_name || '').toLowerCase();
            const phoneDigits = (i.client_phone || '').replace(/\D/g, '');
            if (alv.includes(q)) return true;
            if (agent.includes(q)) return true;
            if (digitsQ && phoneDigits.includes(digitsQ)) return true;
            return false;
        });
    }

    if (view.length === 0) {
        let msg;
        if (q) msg = 'No analyses match your search.';
        else if (flipOnly) msg = 'No calls flagged as a possible program flip.';
        else msg = 'No analyses yet. Upload a call to get started.';
        historyList.innerHTML = `<p class="muted">${esc(msg)}</p>`;
        return;
    }
    historyList.innerHTML = '';
    view.forEach(item => {
        const row = document.createElement('div');
        row.className = 'history-row';
        const isCritical = item.critical_fail === 1;
        const det = (item.determination || 'UNKNOWN').toUpperCase();
        let badgeClass = 'pass';
        if (isCritical || det.includes('CRITICAL')) badgeClass = 'critical';
        else if (det.startsWith('FAIL')) badgeClass = 'fail';

        const when = (item.analyzed_at || '').replace('T', ' ');
        const tagLine = [item.alv_tag, item.agent_name, item.call_date, item.client_phone].filter(Boolean).join(' · ');
        const flipBadge = item.program_flip === 1
            ? '<span class="history-flip-badge" title="Possible program flip — review manually">&#9888; Flip</span>'
            : '';
        row.innerHTML = `
            <div class="history-row-main">
                <div class="history-filename">${esc(item.filename)}${flipBadge}</div>
                ${tagLine ? `<div class="history-tagline">${esc(tagLine)}</div>` : ''}
                <div class="history-meta">${esc(when)} &middot; Approval ${esc(item.approval_score || '')} &middot; Post-Enrollment ${esc(item.enrollment_score || '')}</div>
            </div>
            <div class="history-determination ${badgeClass}">${esc(det)}</div>
        `;
        row.onclick = () => loadHistoryItem(item.id);
        historyList.appendChild(row);
    });
}

async function loadHistoryItem(id) {
    try {
        const resp = await fetch(`/history/${id}`, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        checkAuth(resp);
        if (!resp.ok) throw new Error('Could not load analysis');
        const data = await resp.json();
        const meta = {
            alv_tag: data.alv_tag,
            agent_name: data.agent_name,
            call_date: data.call_date,
            client_phone: data.client_phone,
            filename: data.filename,
            analysis_id: data.id,
            overrides: data.overrides,
        };
        renderResults(data.results, data.transcript, meta);
    } catch (err) {
        historyList.innerHTML = `<p class="error-inline">${esc(err.message)}</p>`;
    }
}

// Build a clean, printable scorecard (no transcript) and open the browser's
// print dialog so the reviewer can Save as PDF and attach it to an email.
function downloadReport(failsOnly = false) {
    const card = document.getElementById('printScorecard');
    if (!card) return;
    card.innerHTML = '';

    const header = document.createElement('div');
    header.className = 'ps-header';
    header.innerHTML =
        `<div class="ps-title">Call QA Scorecard${failsOnly ? ' — Failures Only' : ''}</div>` +
        `<div class="ps-generated">Generated ${esc(new Date().toLocaleString())}</div>`;
    card.appendChild(header);

    // Final determination (carry over PASS / FAIL / CRITICAL coloring)
    const detEl = document.getElementById('determination');
    const detDiv = document.createElement('div');
    const detClass = detEl.classList.contains('pass') ? 'pass'
        : detEl.classList.contains('critical') ? 'critical' : 'fail';
    detDiv.className = `ps-determination ${detClass}`;
    detDiv.textContent = detEl.textContent.trim();
    card.appendChild(detDiv);

    // Possible program flip note
    const flipBanner = document.getElementById('programFlipBanner');
    if (flipBanner && flipBanner.style.display !== 'none') {
        const t = flipBanner.querySelector('.program-flip-title')?.textContent || 'Possible program flip';
        const n = flipBanner.querySelector('.program-flip-note')?.textContent || '';
        const f = document.createElement('div');
        f.className = 'ps-flip';
        f.innerHTML = `<div class="ps-flip-title">${esc(t.trim())}</div>` +
            (n ? `<div class="ps-flip-note">${esc(n.trim())}</div>` : '');
        card.appendChild(f);
    }

    // Ineligible account(s)
    const inel = document.getElementById('ineligibleBanner');
    if (inel && inel.style.display !== 'none') {
        const title = inel.querySelector('.ineligible-title')?.textContent || 'Possible ineligible accounts';
        const d = document.createElement('div');
        d.className = 'ps-ineligible';
        let html = `<div class="ps-ineligible-title">${esc(title.trim())}</div>`;
        inel.querySelectorAll('.ineligible-list li').forEach(li => {
            html += `<div class="ps-ineligible-item">${esc(li.textContent.replace(/\s+/g, ' ').trim())}</div>`;
        });
        d.innerHTML = html;
        card.appendChild(d);
    }

    // Collections-context note
    const coll = document.getElementById('collectionsBanner');
    if (coll && coll.style.display !== 'none') {
        const t = coll.querySelector('.collections-title')?.textContent || 'Accounts appear to be in collections';
        const n = coll.querySelector('.collections-note')?.textContent || '';
        const c = document.createElement('div');
        c.className = 'ps-collections';
        c.innerHTML = `<div class="ps-collections-title">${esc(t.trim())}</div>` +
            (n ? `<div class="ps-collections-note">${esc(n.trim())}</div>` : '');
        card.appendChild(c);
    }

    // Call metadata (ALV tag, agent, call date, client phone, file)
    const metaCells = document.querySelectorAll('#callMeta .call-meta-cell');
    let alvValue = '';
    if (metaCells.length) {
        const meta = document.createElement('div');
        meta.className = 'ps-meta';
        metaCells.forEach(cell => {
            const label = cell.querySelector('.call-meta-label')?.textContent || '';
            const value = cell.querySelector('.call-meta-value')?.textContent || '';
            if (label.toLowerCase().includes('alv')) alvValue = value.trim();
            const c = document.createElement('div');
            c.className = 'ps-meta-cell';
            c.innerHTML = `<span class="ps-meta-label">${esc(label)}</span>` +
                `<span class="ps-meta-value">${esc(value)}</span>`;
            meta.appendChild(c);
        });
        card.appendChild(meta);
    }

    // Summary
    const summary = document.getElementById('summaryText').textContent.trim();
    if (summary) {
        const s = document.createElement('div');
        s.className = 'ps-summary';
        s.textContent = summary;
        card.appendChild(s);
    }

    card.appendChild(buildPrintSection('Approval Script', 'approvalItems', 'approvalScore', failsOnly));
    card.appendChild(buildPrintSection('Post-Enrollment Script', 'enrollmentItems', 'enrollmentScore', failsOnly));
    card.appendChild(buildPrintHighRisk());

    // Pre-set the suggested filename via the document title (used by Save as PDF).
    const dateStr = new Date().toISOString().slice(0, 10);
    const baseLabel = alvValue ? `scorecard-${alvValue}` : `qa-scorecard-${dateStr}`;
    const fileLabel = (failsOnly ? `${baseLabel}-fails` : baseLabel)
        .replace(/[^A-Za-z0-9_-]+/g, '-');
    const prevTitle = document.title;
    document.title = fileLabel;
    window.addEventListener('afterprint', function restore() {
        document.title = prevTitle;
        window.removeEventListener('afterprint', restore);
    });

    window.print();
}

function buildPrintSection(heading, itemsId, scoreId, failsOnly = false) {
    const wrap = document.createElement('div');
    wrap.className = 'ps-section';
    const score = document.getElementById(scoreId)?.textContent || '';
    const hdr = document.createElement('div');
    hdr.className = 'ps-section-header';
    hdr.innerHTML = `<span class="ps-section-title">${esc(heading)}</span>` +
        `<span class="ps-section-score">${esc(score)}</span>`;
    wrap.appendChild(hdr);

    let shown = 0;
    document.querySelectorAll(`#${itemsId} .item-row`).forEach(row => {
        const covered = row.querySelector('.item-status')?.classList.contains('covered');
        if (failsOnly && covered) return;  // fails-only report omits passed items
        shown++;
        const name = row.querySelector('.item-name')?.textContent || '';
        const evidence = row.querySelector('.item-evidence')?.textContent || '';
        const ts = row.querySelector('.item-timestamp')?.textContent || '';
        const r = document.createElement('div');
        r.className = 'ps-item';
        r.innerHTML =
            `<span class="ps-item-mark ${covered ? 'covered' : 'not-covered'}">${covered ? '&#10003;' : '&#10007;'}</span>` +
            `<span class="ps-item-body">` +
            `<span class="ps-item-name">${esc(name)}${ts ? ` <span class="ps-item-ts">${esc(ts)}</span>` : ''}</span>` +
            (evidence ? `<span class="ps-item-evidence">${esc(evidence)}</span>` : '') +
            `</span>`;
        wrap.appendChild(r);
    });

    if (failsOnly && shown === 0) {
        const p = document.createElement('div');
        p.className = 'ps-noissues';
        p.textContent = 'No failures in this section.';
        wrap.appendChild(p);
    }
    return wrap;
}

function buildPrintHighRisk() {
    const wrap = document.createElement('div');
    wrap.className = 'ps-section';
    const score = document.getElementById('highRiskScore')?.textContent || '';
    const hdr = document.createElement('div');
    hdr.className = 'ps-section-header';
    hdr.innerHTML = `<span class="ps-section-title">High-Risk Phrases</span>` +
        `<span class="ps-section-score">${esc(score)}</span>`;
    wrap.appendChild(hdr);

    const hrItems = document.getElementById('highRiskItems');
    if (hrItems.querySelector('.no-issues')) {
        const p = document.createElement('div');
        p.className = 'ps-noissues';
        p.textContent = 'No high-risk phrases detected.';
        wrap.appendChild(p);
    } else {
        hrItems.querySelectorAll('.high-risk-item').forEach(item => {
            const phrase = item.querySelector('.phrase')?.textContent || '';
            const quote = item.querySelector('.quote')?.textContent || '';
            const violation = item.querySelector('.violation-note')?.textContent || '';
            const d = document.createElement('div');
            d.className = 'ps-hr-item';
            d.innerHTML = `<div class="ps-hr-phrase">${esc(phrase)}</div>` +
                (quote ? `<div class="ps-hr-quote">${esc(quote)}</div>` : '') +
                (violation ? `<div class="ps-hr-violation">${esc(violation)}</div>` : '');
            wrap.appendChild(d);
        });
    }
    return wrap;
}

let currentWriteupMode = 'written';

function openWriteupModal(mode) {
    if (!currentAnalysisId) return;
    currentWriteupMode = mode === 'verbal' ? 'verbal' : 'written';
    writeupModalTitle.textContent =
        currentWriteupMode === 'verbal' ? 'Generate Verbal Warning' : 'Generate Written Warning';
    writeupError.textContent = '';
    writeupAgentInput.value = currentAgentName || '';
    writeupGenerateBtn.disabled = false;
    writeupGenerateBtn.textContent = 'Generate';
    writeupModal.style.display = 'flex';
    setTimeout(() => {
        writeupAgentInput.focus();
        const v = writeupAgentInput.value;
        writeupAgentInput.setSelectionRange(v.length, v.length);
    }, 0);
}

function closeWriteupModal() {
    writeupModal.style.display = 'none';
}

async function generateWriteup() {
    const name = (writeupAgentInput.value || '').trim().replace(/\s+/g, ' ');
    if (!name) {
        writeupError.textContent = 'Agent name is required.';
        writeupAgentInput.focus();
        return;
    }
    if (!currentAnalysisId) {
        writeupError.textContent = 'No analysis loaded.';
        return;
    }
    writeupError.textContent = '';
    writeupGenerateBtn.disabled = true;
    writeupGenerateBtn.textContent = 'Generating...';
    // Flush any debounced override save so the write-up sees the latest state.
    if (saveTimer) {
        clearTimeout(saveTimer);
        saveTimer = null;
    }
    try {
        await persistOverrides();
    } catch {}
    try {
        const resp = await fetch(`/writeup/${currentAnalysisId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify({ agent_name: name, mode: currentWriteupMode }),
        });
        if (resp.status === 401) { window.location = '/login'; return; }
        if (!resp.ok) {
            let msg = 'Generation failed.';
            try {
                const errData = await resp.json();
                if (errData && errData.error) msg = errData.error;
            } catch {}
            throw new Error(msg);
        }
        const blob = await resp.blob();
        const cd = resp.headers.get('Content-Disposition') || '';
        const m = cd.match(/filename="?([^";]+)"?/i);
        const filename = m ? m[1]
            : (currentWriteupMode === 'verbal' ? 'VerbalWarning.docx' : 'WrittenWarning.docx');
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        currentAgentName = name;
        closeWriteupModal();
    } catch (err) {
        writeupError.textContent = err.message || 'Generation failed.';
        writeupGenerateBtn.disabled = false;
        writeupGenerateBtn.textContent = 'Generate';
    }
}

function esc(str) {
    const el = document.createElement('span');
    el.textContent = str;
    return el.innerHTML;
}
