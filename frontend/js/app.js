/**
 * OCR Studio - Main Application Controller
 * Handles view routing, state management, event listeners, and WebSocket notifications.
 */

import * as api from './api.js';
import { connectWs } from './websocket.js';

// Application State
let currentJobId = null;
let currentOutputFilename = null;
let appSettings = {};
const ignoredJobIds = new Set();
let previewCurrentPage = 0;
let previewTotalPages = 1;
let currentPdfFilename = null;
let previewPagesList = [];
let pageConfidenceMap = {};

// DOM Elements cache
const DOM = {
  // Views
  viewDashboard: document.getElementById('view-dashboard'),
  viewProcessing: document.getElementById('view-processing'),
  viewResults: document.getElementById('view-results'),
  modalSettings: document.getElementById('modal-settings'),

  // Header & Status
  statusDot: document.getElementById('status-dot'),
  statusText: document.getElementById('status-text'),
  btnSettings: document.getElementById('btn-settings'),

  // Settings Form
  formSettings: document.getElementById('form-settings'),
  inputServer: document.getElementById('input-server'),
  selectModel: document.getElementById('select-model'),
  inputWorkers: document.getElementById('input-workers'),
  inputPagesGroup: document.getElementById('input-pages-group'),
  selectImgDim: document.getElementById('select-img-dim'),
  inputOutputDir: document.getElementById('input-output-dir'),
  inputPageRange: document.getElementById('input-page-range'),
  btnSettingsCancel: document.getElementById('btn-settings-cancel'),
  btnOpenLogs: document.getElementById('btn-open-logs'),

  // Dashboard / Upload
  dropZone: document.getElementById('drop-zone'),
  btnBrowse: document.getElementById('btn-browse'),
  fileInput: document.getElementById('file-input'),
  recentJobsList: document.getElementById('recent-jobs-list'),
  btnClearHistory: document.getElementById('btn-clear-history'),

  // Processing
  processingFileLabel: document.getElementById('processing-file-label'),
  processingPathsLabel: document.getElementById('processing-paths-label'),
  txtProgressStatus: document.getElementById('txt-progress-status'),
  txtEtr: document.getElementById('txt-etr'),
  barProgressFill: document.getElementById('bar-progress-fill'),
  pagesGrid: document.getElementById('pages-grid'),
  terminalLog: document.getElementById('terminal-log'),
  btnCancelProcessing: document.getElementById('btn-cancel-processing'),

  // Results
  resultStatusLabel: document.getElementById('result-status-label'),
  txtMarkdownPreview: document.getElementById('txt-markdown-preview'),
  previewPageImg: document.getElementById('preview-page-img'),
  previewErrorOverlay: document.getElementById('preview-error-overlay'),
  btnPreviewPrev: document.getElementById('btn-preview-prev'),
  btnPreviewNext: document.getElementById('btn-preview-next'),
  txtPreviewPageLabel: document.getElementById('txt-preview-page-label'),
  previewConfidenceBadge: document.getElementById('preview-confidence-badge'),
  btnResultsCopy: document.getElementById('btn-results-copy'),
  btnResultsDownload: document.getElementById('btn-results-download'),
  btnResultsDownloadWord: document.getElementById('btn-results-download-word'),
  btnResultsDownloadHtml: document.getElementById('btn-results-download-html'),
  btnResultsNew: document.getElementById('btn-results-new'),
};

/**
 * Switch active views using classes
 */
function showView(viewId) {
  const views = [DOM.viewDashboard, DOM.viewProcessing, DOM.viewResults];
  views.forEach(view => {
    if (view.id === viewId) {
      view.classList.remove('is-hidden');
      view.classList.add('is-active');
    } else {
      view.classList.remove('is-active');
      view.classList.add('is-hidden');
    }
  });
}

/**
 * Periodically check LM Studio Server health
 */
async function checkHealthStatus() {
  try {
    const health = await api.checkHealth();
    if (health.reachable) {
      DOM.statusDot.className = 'status-dot online pulse';
      DOM.statusText.textContent = health.model_loaded 
        ? `LM Studio: ${health.model_loaded}`
        : 'LM Studio: Connected';
    } else {
      DOM.statusDot.className = 'status-dot offline';
      DOM.statusText.textContent = `LM Studio: Disconnected (${health.error || 'Unknown error'})`;
    }
  } catch (err) {
    DOM.statusDot.className = 'status-dot offline';
    DOM.statusText.textContent = 'LM Studio: Server unreachable';
  }
}

/**
 * Fetches available models and populates the model dropdown
 */
async function populateModelsDropdown(savedModel) {
  const select = DOM.selectModel;
  if (!select) return;

  // Show loading option
  select.innerHTML = '<option value="" disabled selected>Loading models...</option>';

  let models = [];
  try {
    models = await api.getModels();
  } catch (err) {
    console.error('Failed to fetch models:', err);
  }

  select.innerHTML = '';

  // Check if we need to add the saved model as a temporary option (fallback/offline safety)
  if (savedModel && !models.includes(savedModel)) {
    const opt = document.createElement('option');
    opt.value = savedModel;
    opt.textContent = `${savedModel} (Offline/Unlisted)`;
    select.appendChild(opt);
  }

  // Populate the returned models
  models.forEach(modelId => {
    const opt = document.createElement('option');
    opt.value = modelId;
    opt.textContent = modelId;
    select.appendChild(opt);
  });

  // Select the saved model
  if (savedModel) {
    select.value = savedModel;
  } else if (select.options.length > 0) {
    select.selectedIndex = 0;
  }
}

/**
 * Load and display application settings
 */
async function loadSettings() {
  try {
    appSettings = await api.getSettings();
    DOM.inputServer.value = appSettings.server_url || '';
    DOM.inputWorkers.value = appSettings.workers || 4;
    DOM.inputPagesGroup.value = appSettings.pages_per_group || 20;
    DOM.selectImgDim.value = appSettings.target_longest_image_dim || 1024;
    DOM.inputOutputDir.value = appSettings.output_dir || '';
    DOM.inputPageRange.value = appSettings.page_range || '';

    // Dynamically populate model dropdown
    await populateModelsDropdown(appSettings.model);
  } catch (err) {
    console.error('Failed to load settings:', err);
  }
}

/**
 * Save settings from settings modal form
 */
async function saveSettings(e) {
  e.preventDefault();
  const config = {
    server_url: DOM.inputServer.value.trim(),
    model: DOM.selectModel.value ? DOM.selectModel.value.trim() : '',
    workers: parseInt(DOM.inputWorkers.value, 10),
    pages_per_group: parseInt(DOM.inputPagesGroup.value, 10),
    target_longest_image_dim: parseInt(DOM.selectImgDim.value, 10),
    output_dir: DOM.inputOutputDir.value.trim(),
    page_range: DOM.inputPageRange.value.trim(),
  };

  try {
    await api.updateSettings(config);
    DOM.modalSettings.classList.add('is-hidden');
    await checkHealthStatus();
  } catch (err) {
    alert(`Failed to save settings: ${err.message}`);
  }
}

/**
 * Render list of recent jobs
 */
function renderRecentJobs(jobs) {
  DOM.recentJobsList.innerHTML = '';
  if (!jobs || jobs.length === 0) {
    DOM.recentJobsList.innerHTML = '<li class="job-item"><span class="job-meta">No recent jobs found.</span></li>';
    return;
  }

  jobs.forEach(job => {
    const li = document.createElement('li');
    li.className = 'job-item';
    
    let statusClass = 'badge--success';
    if (job.status === 'failed') statusClass = 'badge--error';
    if (job.status === 'processing') statusClass = 'badge--processing';
    if (job.status === 'queued') statusClass = 'badge--queued';

    const timestamp = job.status === 'completed' && job.completed_at ? job.completed_at : job.created_at;
    const timeString = timestamp ? new Date(timestamp).toLocaleTimeString() : 'Unknown';

    li.innerHTML = `
      <div class="job-info">
        <span class="job-name" title="${job.pdf_filename}">${job.pdf_filename}</span>
        <span class="job-meta">${job.pages_total} pages &bull; ${timeString}</span>
      </div>
      <span class="job-status-badge ${statusClass}">${job.status}</span>
    `;

    // Clicking completed job displays results
    if (job.status === 'completed' && job.output_filename) {
      li.style.cursor = 'pointer';
      li.addEventListener('click', () => {
        showResults(job.pdf_filename, job.output_filename);
      });
    }

    DOM.recentJobsList.appendChild(li);
  });
}

/**
 * Fetch and refresh recent jobs list
 */
async function refreshRecentJobs() {
  try {
    const jobs = await api.getRecentJobs();
    renderRecentJobs(jobs);
  } catch (err) {
    console.error('Failed to load recent jobs:', err);
  }
}

/**
 * Initialize dynamic page grid cards
 */
function initPageCards(totalPages) {
  DOM.pagesGrid.innerHTML = '';
  for (let i = 1; i <= totalPages; i++) {
    const card = document.createElement('div');
    card.className = 'page-card state-pending glass-panel';
    card.id = `page-card-${i}`;
    card.innerHTML = `
      <span class="page-num">${i}</span>
      <span class="page-status-text" id="page-status-label-${i}">Pending</span>
    `;
    DOM.pagesGrid.appendChild(card);
  }
}

/**
 * Update class status on a single page card
 */
function updatePageCardStatus(pageNum, status) {
  const card = document.getElementById(`page-card-${pageNum}`);
  const label = document.getElementById(`page-status-label-${pageNum}`);
  if (!card || !label) return;

  card.className = 'page-card glass-panel'; // reset state class
  
  if (status === 'processing') {
    card.classList.add('state-processing');
    label.textContent = 'Active';
  } else if (status === 'complete') {
    card.classList.add('state-done');
    label.textContent = 'Done';
  } else if (status === 'failed') {
    card.classList.add('state-error');
    label.textContent = 'Error';
  } else {
    card.classList.add('state-pending');
    label.textContent = 'Pending';
  }
}

/**
 * Format estimated time remaining seconds
 */
function formatEta(seconds) {
  if (seconds === null || seconds === undefined || seconds < 0) {
    return 'Calculating...';
  }
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}m ${secs}s`;
}

/**
 * Append line to terminal output
 */
function appendLog(message, type = 'info') {
  const line = document.createElement('div');
  line.className = `terminal-line ${type}`;
  const timestamp = new Date().toLocaleTimeString();
  line.textContent = `[${timestamp}] ${message}`;
  DOM.terminalLog.appendChild(line);
  DOM.terminalLog.scrollTop = DOM.terminalLog.scrollHeight;
}

/**
 * Handle incoming WebSocket progress updates
 */
async function handleWebSocketMessage(data) {
  // Ignore updates for explicitly cancelled jobs
  if (ignoredJobIds.has(data.job_id)) return;

  // If we don't have a current job ID set (e.g. browser refresh during process), bind to it
  if (!currentJobId && (data.event !== 'job_complete' && data.event !== 'job_failed')) {
    currentJobId = data.job_id;
    DOM.processingFileLabel.textContent = 'Processing active background job';
    DOM.processingPathsLabel.textContent = `Input: uploads/... | Output: ${appSettings.output_dir || 'Default (output/)'}`;
    showView('view-processing');

    api.getRecentJobs().then(jobs => {
      const job = jobs.find(j => j.job_id === currentJobId);
      if (job) {
        DOM.processingFileLabel.textContent = `Analyzing: ${job.pdf_filename}`;
        DOM.processingPathsLabel.textContent = `Input: uploads/${job.pdf_filename} | Output: ${appSettings.output_dir || 'Default (output/)'}`;
      }
    }).catch(err => console.error('Failed to resolve job info:', err));

    if (data.pages_total) {
      initPageCards(data.pages_total);
    }
  }

  // Handle snapping to next processing job automatically
  if (data.event === 'job_start') {
    currentJobId = data.job_id;
    DOM.terminalLog.innerHTML = '';
    DOM.txtProgressStatus.textContent = data.message || 'Starting job...';
    DOM.barProgressFill.style.width = '0%';
    DOM.txtEtr.textContent = 'Calculating ETA...';
    
    api.getRecentJobs().then(jobs => {
      const job = jobs.find(j => j.job_id === currentJobId);
      if (job) {
        DOM.processingFileLabel.textContent = `Analyzing: ${job.pdf_filename}`;
        DOM.processingPathsLabel.textContent = `Input: uploads/${job.pdf_filename} | Output: ${appSettings.output_dir || 'Default (output/)'}`;
      }
    }).catch(err => console.error('Failed to resolve job info:', err));

    if (data.pages_total) {
      initPageCards(data.pages_total);
    }
    
    // Only snap the view if the user is not actively browsing another view. Actually, let's always snap as requested.
    showView('view-processing');
    return;
  }

  // Verify message matches our current active job
  if (data.job_id !== currentJobId) return;

  // Log events
  if (data.message) {
    let logType = 'info';
    if (data.event === 'page_complete') logType = 'success';
    if (data.event === 'page_failed') logType = 'warning';
    if (data.event === 'job_failed') logType = 'error';
    appendLog(data.message, logType);
  }

  // Update progress bar & labels
  DOM.txtProgressStatus.textContent = data.message || `Processing...`;
  DOM.barProgressFill.style.width = `${data.progress_percent}%`;
  DOM.txtEtr.textContent = `Estimated Time Remaining: ${formatEta(data.eta_seconds)}`;

  // Update cards
  if (data.page_num) {
    if (data.event === 'page_start') {
      updatePageCardStatus(data.page_num, 'processing');
    } else if (data.event === 'page_complete') {
      updatePageCardStatus(data.page_num, 'complete');
      if (data.confidence !== undefined && data.confidence !== null) {
        pageConfidenceMap[data.page_num] = data.confidence;
      }
    } else if (data.event === 'page_failed') {
      updatePageCardStatus(data.page_num, 'failed');
    }
  }

  // Handle completion
  if (data.event === 'job_complete') {
    appendLog('Job execution finished successfully!', 'success');
    const completedJobId = data.job_id;
    setTimeout(async () => {
      try {
        const jobs = await api.getRecentJobs();
        await refreshRecentJobs();
        
        // Only show results if this job is STILL the current job in the UI
        if (currentJobId === completedJobId) {
          const matchingJob = jobs.find(j => j.job_id === completedJobId);
          if (matchingJob && matchingJob.output_filename) {
            showResults(matchingJob.pdf_filename, matchingJob.output_filename);
          } else {
            alert('OCR job finished, but output file info was not found.');
            showView('view-dashboard');
          }
        }
      } catch (err) {
        console.error('Error fetching job details on completion:', err);
      }
    }, 1500);
  }

  if (data.event === 'job_failed') {
    alert(`Job failed: ${data.message}`);
    setTimeout(() => {
      showView('view-dashboard');
      refreshRecentJobs();
    }, 1500);
  }
}

/**
 * Handle document drag & drop files
 */
function setupDragAndDrop() {
  ['dragenter', 'dragover'].forEach(eventName => {
    DOM.dropZone.addEventListener(eventName, (e) => {
      e.preventDefault();
      DOM.dropZone.classList.add('dragover');
    }, false);
  });

  ['dragleave', 'drop'].forEach(eventName => {
    DOM.dropZone.addEventListener(eventName, (e) => {
      e.preventDefault();
      DOM.dropZone.classList.remove('dragover');
    }, false);
  });

  DOM.dropZone.addEventListener('drop', (e) => {
    const dt = e.dataTransfer;
    const pdfFiles = Array.from(dt.files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
    if (pdfFiles.length > 0) {
      handlePdfFiles(pdfFiles);
    } else {
      alert('Only PDF files are accepted.');
    }
  });

  DOM.btnBrowse.addEventListener('click', () => DOM.fileInput.click());
  DOM.fileInput.addEventListener('change', (e) => {
    const pdfFiles = Array.from(e.target.files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
    if (pdfFiles.length > 0) {
      handlePdfFiles(pdfFiles);
    }
  });
}

/**
 * Handle selected/dropped PDFs and run job flow (batching)
 */
async function handlePdfFiles(files) {
  try {
    DOM.dropZone.style.pointerEvents = 'none';
    DOM.dropZone.querySelector('h3').textContent = `Uploading ${files.length} PDF(s)...`;
    DOM.dropZone.querySelector('p').textContent = 'Please wait while we stage your documents';
    
    let firstJobId = null;

    for (const file of files) {
      // 1. Upload file
      const uploadRes = await api.uploadPdf(file);
      
      // 2. Queue OCR job using current settings overrides
      const jobConfig = {
        pdf_filename: uploadRes.filename,
      };
      
      const jobRes = await api.startJob(jobConfig);
      
      // 3. Switch to processing view for the FIRST job in the batch
      if (!firstJobId) {
        firstJobId = jobRes.job_id;
        currentJobId = firstJobId;
        
        DOM.processingFileLabel.textContent = `Analyzing: ${uploadRes.filename}`;
        DOM.processingPathsLabel.textContent = `Input: uploads/${uploadRes.filename} | Output: ${appSettings.output_dir || 'Default (output/)'}`;
        DOM.txtProgressStatus.textContent = 'Queueing pages...';
        DOM.barProgressFill.style.width = '0%';
        DOM.txtEtr.textContent = 'Calculating ETA...';
        DOM.terminalLog.innerHTML = '';
        
        initPageCards(uploadRes.page_count);
        appendLog(`Job initialized with ID: ${currentJobId}`);
        appendLog(`Total pages to process: ${uploadRes.page_count}`);
        
        showView('view-processing');
      }
    }
    
    // Refresh recent jobs sidebar to show all queued items
    await refreshRecentJobs();

  } catch (err) {
    alert(`OCR Batch Initialization failed: ${err.message}`);
  } finally {
    // Reset drop zone state
    DOM.dropZone.style.pointerEvents = 'auto';
    DOM.dropZone.querySelector('h3').textContent = 'Drag & Drop PDF here';
    DOM.dropZone.querySelector('p').textContent = 'or click to browse from your computer';
    DOM.fileInput.value = '';
  }
}

/**
 * Display final results
 */
async function showResults(pdfFilename, outputFilename) {
  currentOutputFilename = outputFilename;
  currentPdfFilename = pdfFilename;
  pageConfidenceMap = {}; // Reset confidence scores for new preview
  DOM.resultStatusLabel.textContent = `Successfully generated Markdown for ${pdfFilename}`;
  DOM.txtMarkdownPreview.value = 'Loading preview content...';
  showView('view-results');

  try {
    // Attempt to restore confidence scores from the backend job state
    try {
      const jobs = await api.getRecentJobs();
      const matchingJob = jobs.find(j => j.output_filename === outputFilename);
      if (matchingJob && matchingJob.page_confidence) {
        for (const [pageNumStr, conf] of Object.entries(matchingJob.page_confidence)) {
          pageConfidenceMap[parseInt(pageNumStr, 10)] = conf;
        }
      }
    } catch (err) {
      console.error('Failed to restore page confidence scores:', err);
    }

    // Fetch file text content directly using the download endpoint
    const fileUrl = `/api/download/${encodeURIComponent(outputFilename)}`;
    const res = await fetch(fileUrl);
    if (res.ok) {
      const markdown = await res.text();
      DOM.txtMarkdownPreview.value = markdown;

      // Parse processed page list from markdown headers
      const markerRegex = /<!-- PAGE (\d{3}) -->/g;
      let match;
      previewPagesList = [];
      while ((match = markerRegex.exec(markdown)) !== null) {
        previewPagesList.push(parseInt(match[1], 10));
      }

      if (previewPagesList.length > 0) {
        previewTotalPages = Math.max(...previewPagesList);
        previewCurrentPage = 0;
        loadPreviewPage(0);
      } else {
        previewPagesList = [1];
        previewTotalPages = 1;
        previewCurrentPage = 0;
        DOM.previewPageImg.src = '';
        DOM.previewErrorOverlay.classList.remove('is-hidden');
        DOM.txtPreviewPageLabel.textContent = 'Page 0 of 0';
        DOM.btnPreviewPrev.disabled = true;
        DOM.btnPreviewNext.disabled = true;
      }
    } else {
      DOM.txtMarkdownPreview.value = `Failed to load preview. You can still try downloading the file: ${outputFilename}`;
    }
  } catch (err) {
    DOM.txtMarkdownPreview.value = `Error loading preview: ${err.message}`;
  }
}

/**
 * Load specific page preview and sync text scrolling
 */
function loadPreviewPage(pageIndex) {
  if (previewPagesList.length === 0) return;

  // Clamp index
  if (pageIndex < 0) pageIndex = 0;
  if (pageIndex >= previewPagesList.length) pageIndex = previewPagesList.length - 1;

  previewCurrentPage = pageIndex;
  const actualPageNum = previewPagesList[previewCurrentPage];

  // Hide the error overlay (if shown previously)
  DOM.previewErrorOverlay.classList.add('is-hidden');

  // Trigger image fetch
  if (currentPdfFilename) {
    DOM.previewPageImg.src = `/api/pdf/${encodeURIComponent(currentPdfFilename)}/page/${actualPageNum}/image`;
  }

  // Update nav label
  DOM.txtPreviewPageLabel.textContent = `Page ${actualPageNum} of ${previewTotalPages}`;

  // Enable/disable prev/next nav buttons
  DOM.btnPreviewPrev.disabled = (previewCurrentPage === 0);
  DOM.btnPreviewNext.disabled = (previewCurrentPage === previewPagesList.length - 1);

  // Update confidence badge
  const score = pageConfidenceMap[actualPageNum];
  updateConfidenceBadge(score);

  // Scroll textarea to the start of this page's content
  scrollTextareaToPage(actualPageNum);
}

/**
 * Align textarea scroll position to a specific page marker
 */
function scrollTextareaToPage(pageNum) {
  const textarea = DOM.txtMarkdownPreview;
  const text = textarea.value;
  const marker = `<!-- PAGE ${pageNum.toString().padStart(3, '0')} -->`;
  const charIndex = text.indexOf(marker);

  if (charIndex !== -1) {
    // Focus and highlight the page tag
    textarea.focus();
    textarea.setSelectionRange(charIndex, charIndex + marker.length);

    // Calculate vertical scroll position using newlines to prevent density misalignment
    const textBefore = text.substring(0, charIndex);
    const linesBefore = textBefore.split('\n').length - 1;
    const totalLines = text.split('\n').length;

    const lineRatio = linesBefore / totalLines;
    textarea.scrollTop = lineRatio * textarea.scrollHeight;
  }
}

/**
 * Update the color-coded confidence badge next to the page navigator
 */
function updateConfidenceBadge(score) {
  const badge = DOM.previewConfidenceBadge;
  if (!badge) return;

  // Reset classes to base
  badge.className = 'confidence-badge';

  if (score === null || score === undefined) {
    badge.textContent = 'N/A';
    badge.classList.add('badge--unknown');
  } else {
    badge.textContent = `${score}% Confidence`;
    if (score >= 90) {
      badge.classList.add('badge--high');
    } else if (score >= 75) {
      badge.classList.add('badge--medium');
    } else {
      badge.classList.add('badge--low');
    }
  }
}

/**
 * Bind UI controls
 */
function bindEvents() {
  // Settings modal
  DOM.btnSettings.addEventListener('click', () => {
    loadSettings();
    DOM.modalSettings.classList.remove('is-hidden');
  });
  DOM.btnSettingsCancel.addEventListener('click', () => {
    DOM.modalSettings.classList.add('is-hidden');
  });
  DOM.btnOpenLogs.addEventListener('click', async () => {
    try {
      await api.openLogs();
    } catch (err) {
      alert(`Failed to open logs: ${err.message}`);
    }
  });
  DOM.formSettings.addEventListener('submit', saveSettings);

  // Cancel Job (Reset UI state and abort backend task)
  DOM.btnCancelProcessing.addEventListener('click', async () => {
    if (confirm('Are you sure you want to cancel this OCR job?')) {
      const idToCancel = currentJobId;
      if (idToCancel) {
        ignoredJobIds.add(idToCancel);
        try {
          await api.cancelJob(idToCancel);
        } catch (err) {
          console.error('Error calling cancel on backend:', err);
        }
      }
      currentJobId = null;
      showView('view-dashboard');
      refreshRecentJobs();
    }
  });

  // Clear Job History
  DOM.btnClearHistory.addEventListener('click', async () => {
    if (confirm('Are you sure you want to clear all job history?')) {
      try {
        await api.clearJobs();
        await refreshRecentJobs();
      } catch (err) {
        alert(`Failed to clear history: ${err.message}`);
      }
    }
  });

  // Results screen actions
  DOM.btnResultsCopy.addEventListener('click', () => {
    DOM.txtMarkdownPreview.select();
    navigator.clipboard.writeText(DOM.txtMarkdownPreview.value)
      .then(() => alert('Markdown copied to clipboard!'))
      .catch(err => alert(`Failed to copy text: ${err}`));
  });

  DOM.btnResultsDownload.addEventListener('click', () => {
    if (currentOutputFilename) {
      api.downloadResult(currentOutputFilename);
    }
  });

  DOM.btnResultsDownloadWord.addEventListener('click', () => {
    if (currentOutputFilename) {
      api.downloadResult(currentOutputFilename, 'docx');
    }
  });

  DOM.btnResultsDownloadHtml.addEventListener('click', () => {
    if (currentOutputFilename) {
      api.downloadResult(currentOutputFilename, 'html');
    }
  });

  DOM.btnResultsNew.addEventListener('click', () => {
    currentJobId = null;
    currentOutputFilename = null;
    showView('view-dashboard');
    refreshRecentJobs();
  });

  // PDF Preview actions
  DOM.btnPreviewPrev.addEventListener('click', () => {
    if (previewCurrentPage > 0) {
      loadPreviewPage(previewCurrentPage - 1);
    }
  });

  DOM.btnPreviewNext.addEventListener('click', () => {
    if (previewCurrentPage < previewPagesList.length - 1) {
      loadPreviewPage(previewCurrentPage + 1);
    }
  });
}

// Initialise App
document.addEventListener('DOMContentLoaded', async () => {
  // Bind actions
  bindEvents();
  setupDragAndDrop();

  // Load initial configurations
  await loadSettings();
  await refreshRecentJobs();
  await checkHealthStatus();

  // Ping health every 30 seconds
  setInterval(checkHealthStatus, 30000);

  // Start WS Progress updates
  connectWs(handleWebSocketMessage);
});
