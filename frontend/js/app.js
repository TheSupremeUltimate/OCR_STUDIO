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
let activeJobTokenLogprobs = {};
let zoneDrawing = false;
let zoneStartX = 0;
let zoneStartY = 0;
let zoneCoordinates = null;
let activeLowConfidenceSpan = null;

// Zoom and Pan State for PDF Preview
let zoomScale = 1.0;
let panX = 0;
let panY = 0;
let isPanning = false;
let panStartX = 0;
let panStartY = 0;

// Markdown Preview Font Zoom State
let mdZoomScale = 1.0;

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
  inputCustomGlossary: document.getElementById('input-custom-glossary'),
  inputStrictMode: document.getElementById('input-strict-mode'),
  selectReadingDirection: document.getElementById('select-reading-direction'),
  selectDocumentStructure: document.getElementById('select-document-structure'),
  inputFilterBinarize: document.getElementById('input-filter-binarize'),
  inputFilterContrast: document.getElementById('input-filter-contrast'),
  inputFilterDespeckle: document.getElementById('input-filter-despeckle'),
  inputConsensusMode: document.getElementById('input-consensus-mode'),
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
  analyticsRuntime: document.getElementById('analytics-runtime'),
  analyticsConfidence: document.getElementById('analytics-confidence'),
  analyticsRetries: document.getElementById('analytics-retries'),
  btnResultsCopy: document.getElementById('btn-results-copy'),
  btnResultsDownload: document.getElementById('btn-results-download'),
  btnResultsDownloadWord: document.getElementById('btn-results-download-word'),
  btnResultsDownloadHtml: document.getElementById('btn-results-download-html'),
  btnResultsNew: document.getElementById('btn-results-new'),
  btnResultsTranslate: document.getElementById('btn-results-translate'),
  zoneCanvas: document.getElementById('zone-canvas'),
  btnRerunZone: document.getElementById('btn-rerun-zone'),
  previewZoomWrapper: document.getElementById('preview-zoom-wrapper'),
  btnZoomOut: document.getElementById('btn-zoom-out'),
  btnZoomIn: document.getElementById('btn-zoom-in'),
  btnZoomReset: document.getElementById('btn-zoom-reset'),
  btnMdZoomOut: document.getElementById('btn-md-zoom-out'),
  btnMdZoomIn: document.getElementById('btn-md-zoom-in'),
  btnMdZoomReset: document.getElementById('btn-md-zoom-reset'),
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
async function populateModelsDropdown(savedModel, savedTranslationModel) {
  const select = DOM.selectModel;
  const selectTrans = document.getElementById('select-translation-model');
  if (!select) return;

  // Show loading option
  select.innerHTML = '<option value="" disabled selected>Loading models...</option>';
  if (selectTrans) selectTrans.innerHTML = '<option value="" disabled selected>Loading models...</option>';

  let models = [];
  try {
    models = await api.getModels();
  } catch (err) {
    console.error('Failed to fetch models:', err);
  }

  select.innerHTML = '';
  if (selectTrans) selectTrans.innerHTML = '<option value="">Default (Same as OCR Model)</option>';

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
    
    if (selectTrans) {
      const optTrans = document.createElement('option');
      optTrans.value = modelId;
      optTrans.textContent = modelId;
      selectTrans.appendChild(optTrans);
    }
  });

  // Select the saved model
  if (savedModel) {
    select.value = savedModel;
  } else if (select.options.length > 0) {
    select.selectedIndex = 0;
  }
  
  if (selectTrans && savedTranslationModel) {
    selectTrans.value = savedTranslationModel;
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
    DOM.inputPageRange.value = appSettings.page_range || '';
    DOM.inputCustomGlossary.value = appSettings.custom_glossary || '';
    DOM.inputStrictMode.checked = !!appSettings.strict_mode;
    DOM.selectReadingDirection.value = appSettings.reading_direction || 'Default';
    DOM.selectDocumentStructure.value = appSettings.document_structure || 'Standard';
    DOM.inputFilterBinarize.checked = !!appSettings.binarize;
    DOM.inputFilterContrast.checked = !!appSettings.high_contrast;
    DOM.inputFilterDespeckle.checked = !!appSettings.despeckle;
    DOM.inputConsensusMode.checked = !!appSettings.consensus_mode;

    // Dynamically populate model dropdown
    await populateModelsDropdown(appSettings.model, appSettings.translation_model);

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
    translation_model: document.getElementById('select-translation-model')?.value ? document.getElementById('select-translation-model').value.trim() : '',
    workers: parseInt(DOM.inputWorkers.value, 10),
    pages_per_group: parseInt(DOM.inputPagesGroup.value, 10),
    target_longest_image_dim: parseInt(DOM.selectImgDim.value, 10),
    output_dir: DOM.inputOutputDir.value.trim(),
    page_range: DOM.inputPageRange.value.trim(),
    custom_glossary: DOM.inputCustomGlossary.value.trim(),
    strict_mode: DOM.inputStrictMode.checked,
    reading_direction: DOM.selectReadingDirection.value,
    document_structure: DOM.selectDocumentStructure.value,
    binarize: DOM.inputFilterBinarize.checked,
    high_contrast: DOM.inputFilterContrast.checked,
    despeckle: DOM.inputFilterDespeckle.checked,
    consensus_mode: DOM.inputConsensusMode.checked,
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
      if (data.token_logprobs !== undefined && data.token_logprobs !== null) {
        activeJobTokenLogprobs[data.page_num] = data.token_logprobs;
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
    let matchingJob = null;
    try {
      const jobs = await api.getRecentJobs();
      matchingJob = jobs.find(j => j.output_filename === outputFilename);
      
      // Render Job-Level Analytics diagnostics summary
      if (matchingJob) {
        if (matchingJob.total_runtime !== undefined && matchingJob.total_runtime !== null) {
          DOM.analyticsRuntime.textContent = `${matchingJob.total_runtime}s`;
        } else {
          DOM.analyticsRuntime.textContent = '--';
        }

        if (matchingJob.average_confidence !== undefined && matchingJob.average_confidence !== null) {
          const avgConf = matchingJob.average_confidence;
          DOM.analyticsConfidence.textContent = `${avgConf}%`;
          DOM.analyticsConfidence.className = 'analytics-value';
          if (avgConf >= 90.0) {
            DOM.analyticsConfidence.style.color = '#00d4aa';
          } else if (avgConf >= 75.0) {
            DOM.analyticsConfidence.style.color = '#fbbf24';
          } else {
            DOM.analyticsConfidence.style.color = '#ef4444';
          }
        } else {
          DOM.analyticsConfidence.textContent = '--';
          DOM.analyticsConfidence.style.color = '';
        }

        if (matchingJob.total_retries !== undefined && matchingJob.total_retries !== null) {
          DOM.analyticsRetries.textContent = matchingJob.total_retries;
        } else {
          DOM.analyticsRetries.textContent = '0';
        }
      } else {
        DOM.analyticsRuntime.textContent = '--';
        DOM.analyticsConfidence.textContent = '--';
        DOM.analyticsConfidence.style.color = '';
        DOM.analyticsRetries.textContent = '--';
      }

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
      
      activeJobTokenLogprobs = {};
      if (matchingJob && matchingJob.page_token_logprobs) {
        activeJobTokenLogprobs = matchingJob.page_token_logprobs;
      }

      const pages = parseMarkdownIntoPages(markdown);
      DOM.txtMarkdownPreview.innerHTML = '';
      pages.forEach(p => {
        const block = document.createElement('div');
        block.className = 'ocr-page-block';
        block.setAttribute('data-page', p.page_num);

        const header = document.createElement('div');
        header.className = 'ocr-page-header';
        header.setAttribute('contenteditable', 'false');
        header.textContent = `<!-- PAGE ${String(p.page_num).padStart(3, '0')} -->`;

        const contentDiv = document.createElement('div');
        contentDiv.className = 'ocr-page-content';
        contentDiv.setAttribute('contenteditable', 'true');
        
        const logprobs = activeJobTokenLogprobs[p.page_num] || activeJobTokenLogprobs[String(p.page_num)];
        contentDiv.innerHTML = renderPageContentHTML(p.content, logprobs);

        contentDiv.addEventListener('input', triggerAutoSave);

        block.appendChild(header);
        block.appendChild(contentDiv);
        DOM.txtMarkdownPreview.appendChild(block);
      });

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
      DOM.txtMarkdownPreview.textContent = `Failed to load preview. You can still try downloading the file: ${outputFilename}`;
    }
  } catch (err) {
    DOM.txtMarkdownPreview.textContent = `Error loading preview: ${err.message}`;
  }
}

/**
 * Load specific page preview and sync text scrolling
 */
function loadPreviewPage(pageIndex) {
  if (previewPagesList.length === 0) return;

  // Reset zoom and pan on page change
  zoomScale = 1.0;
  panX = 0;
  panY = 0;
  if (DOM.previewZoomWrapper) {
    DOM.previewZoomWrapper.style.transform = 'translate(0px, 0px) scale(1)';
  }

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

  // Clear zoning canvas on page change
  const canvas = DOM.zoneCanvas;
  if (canvas) {
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
  DOM.btnRerunZone.classList.add('is-hidden');
  zoneCoordinates = null;

  // Scroll textarea to the start of this page's content
  scrollTextareaToPage(actualPageNum);
}

/**
 * Align textarea scroll position to a specific page marker
 */
function scrollTextareaToPage(pageNum) {
  const previewContainer = DOM.txtMarkdownPreview;
  const block = previewContainer.querySelector(`.ocr-page-block[data-page="${pageNum}"]`);
  if (block) {
    block.scrollIntoView({ behavior: 'smooth', block: 'start' });
    
    const header = block.querySelector('.ocr-page-header');
    if (header) {
      header.style.color = 'var(--accent-primary)';
      setTimeout(() => {
        header.style.color = '#64748b';
      }, 1500);
    }
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
    const text = getMarkdownText();
    navigator.clipboard.writeText(text)
      .then(() => alert('Markdown copied to clipboard!'))
      .catch(err => alert(`Failed to copy text: ${err}`));
  });

  DOM.btnResultsTranslate.addEventListener('click', async () => {
    if (!currentOutputFilename) return;
    
    DOM.btnResultsTranslate.disabled = true;
    DOM.btnResultsTranslate.textContent = 'Translating...';
    
    await forceSave();
    const text = getMarkdownText();
    
    try {
      const response = await fetch('/api/jobs/translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: text })
      });
      
      if (!response.ok) {
        throw new Error(await response.text());
      }
      
      const result = await response.json();
      
      DOM.txtMarkdownPreview.innerHTML = '';
      const block = document.createElement('div');
      block.className = 'ocr-page-block';
      block.setAttribute('data-page', '1');
      
      const header = document.createElement('div');
      header.className = 'ocr-page-header';
      header.setAttribute('contenteditable', 'false');
      header.textContent = `<!-- TRANSLATED DOCUMENT (English) -->`;
      
      const contentDiv = document.createElement('div');
      contentDiv.className = 'ocr-page-content';
      contentDiv.setAttribute('contenteditable', 'true');
      contentDiv.textContent = result.translated_text;
      
      contentDiv.addEventListener('input', triggerAutoSave);
      
      block.appendChild(header);
      block.appendChild(contentDiv);
      DOM.txtMarkdownPreview.appendChild(block);
      
      await forceSave();
      alert('Document translated successfully!');
    } catch (err) {
      alert(`Translation failed: ${err.message}`);
    } finally {
      DOM.btnResultsTranslate.disabled = false;
      DOM.btnResultsTranslate.textContent = 'Translate to English';
    }
  });

  DOM.btnResultsDownload.addEventListener('click', async () => {
    if (currentOutputFilename) {
      await forceSave();
      api.downloadResult(currentOutputFilename);
    }
  });

  DOM.btnResultsDownloadWord.addEventListener('click', async () => {
    if (currentOutputFilename) {
      await forceSave();
      api.downloadResult(currentOutputFilename, 'docx');
    }
  });

  DOM.btnResultsDownloadHtml.addEventListener('click', async () => {
    if (currentOutputFilename) {
      await forceSave();
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
  setupZoningCanvas();
  setupQAPopover();

  // Align canvas on image load/resize
  DOM.previewPageImg.addEventListener('load', resizeCanvasToImage);
  window.addEventListener('resize', resizeCanvasToImage);

  // Load initial configurations
  await loadSettings();
  await refreshRecentJobs();
  await checkHealthStatus();

  // Ping health every 30 seconds
  setInterval(checkHealthStatus, 30000);

  // Start WS Progress updates
  connectWs(handleWebSocketMessage);
});

// ===========================================================================
// Interactive UI Suite Helpers (Phase 4.10)
// ===========================================================================

function resizeCanvasToImage() {
  const img = DOM.previewPageImg;
  const canvas = DOM.zoneCanvas;
  if (!img || !canvas) return;
  
  if (img.complete && img.naturalWidth) {
    canvas.style.left = `${img.offsetLeft}px`;
    canvas.style.top = `${img.offsetTop}px`;
    canvas.style.width = `${img.offsetWidth}px`;
    canvas.style.height = `${img.offsetHeight}px`;
    canvas.width = img.offsetWidth;
    canvas.height = img.offsetHeight;
  }
}

function parseMarkdownIntoPages(markdown) {
  const pageRegex = /<!-- PAGE (\d{3}) -->/g;
  const pages = [];
  let match;
  let lastIndex = 0;
  let lastPageNum = null;

  while ((match = pageRegex.exec(markdown)) !== null) {
    if (lastPageNum !== null) {
      pages.push({
        page_num: lastPageNum,
        content: markdown.substring(lastIndex, match.index)
      });
    }
    lastPageNum = parseInt(match[1], 10);
    lastIndex = match.index + match[0].length;
  }

  if (lastPageNum !== null) {
    pages.push({
      page_num: lastPageNum,
      content: markdown.substring(lastIndex)
    });
  }

  if (pages.length === 0 && markdown.trim()) {
    pages.push({
      page_num: 1,
      content: markdown
    });
  }

  return pages;
}

function renderPageContentHTML(text, tokenLogprobs) {
  if (!tokenLogprobs || tokenLogprobs.length === 0) {
    return escapeHtml(text);
  }

  let html = '';
  tokenLogprobs.forEach(item => {
    const tokenStr = item.token;
    const confidence = item.confidence;
    const topLogprobs = item.top_logprobs || [];
    const escapedToken = escapeHtml(tokenStr);

    if (confidence !== null && confidence < 80.0) {
      const topChoicesAttr = encodeURIComponent(JSON.stringify(topLogprobs));
      html += `<span class="low-confidence" data-confidence="${confidence}" data-original="${escapedToken}" data-top-choices="${topChoicesAttr}">${escapedToken}</span>`;
    } else {
      html += escapedToken;
    }
  });

  return html;
}

function escapeHtml(string) {
  return String(string)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function getMarkdownText() {
  const blocks = DOM.txtMarkdownPreview.querySelectorAll('.ocr-page-block');
  if (blocks.length === 0) {
    return DOM.txtMarkdownPreview.textContent || '';
  }

  const pagesText = [];
  blocks.forEach(block => {
    const header = block.querySelector('.ocr-page-header');
    const content = block.querySelector('.ocr-page-content');
    if (header && content) {
      // Use textContent instead of innerText to preserve spacing/indentation exactly as approved.
      const pageText = content.textContent || '';
      pagesText.push(`${header.textContent}\n${pageText}\n\n`);
    }
  });

  return pagesText.join('');
}

let saveTimeout = null;
function triggerAutoSave() {
  if (saveTimeout) clearTimeout(saveTimeout);
  saveTimeout = setTimeout(forceSave, 1500);
}

async function forceSave() {
  if (saveTimeout) clearTimeout(saveTimeout);
  if (!currentOutputFilename) return;
  const text = getMarkdownText();
  try {
    const response = await fetch(`/api/download/${encodeURIComponent(currentOutputFilename)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: text })
    });
    if (!response.ok) {
      console.error('Failed to save content:', await response.text());
    } else {
      console.log('Document auto-saved');
    }
  } catch (err) {
    console.error('Failed to save edited markdown:', err);
  }
}

function mergeReprocessedText(currentText, newText) {
  if (!currentText || !currentText.trim()) return newText;
  
  const clean = (str) => str.replace(/\s+/g, '');
  const cleanNew = clean(newText);
  if (!cleanNew) return currentText;
  
  if (clean(currentText).includes(cleanNew)) {
    return currentText;
  }
  
  let bestScore = 0;
  let bestStart = -1;
  let bestEnd = -1;
  
  const windowSizes = [cleanNew.length];
  for (let i = 1; i <= 8; i++) {
    windowSizes.push(cleanNew.length - i);
    windowSizes.push(cleanNew.length + i);
  }
  
  for (let size of windowSizes) {
    if (size <= 0 || size > currentText.length) continue;
    
    for (let i = 0; i <= currentText.length - size; i++) {
      const candidate = currentText.substr(i, size);
      const cleanCandidate = clean(candidate);
      
      const lcs = getLcsLength(cleanCandidate, cleanNew);
      const score = lcs / Math.max(cleanCandidate.length, cleanNew.length);
      
      if (score > bestScore && score > 0.4) {
        bestScore = score;
        bestStart = i;
        bestEnd = i + size;
      }
    }
  }
  
  if (bestStart !== -1 && bestScore >= 0.5) {
    return currentText.slice(0, bestStart) + newText + currentText.slice(bestEnd);
  }
  
  const selection = window.getSelection();
  if (selection && selection.rangeCount > 0) {
    const range = selection.getRangeAt(0);
    if (range.commonAncestorContainer && range.commonAncestorContainer.parentElement && range.commonAncestorContainer.parentElement.closest('.ocr-page-content')) {
      range.deleteContents();
      range.insertNode(document.createTextNode(newText));
      return null;
    }
  }
  
  return currentText + '\n' + newText;
}

function getLcsLength(s1, s2) {
  const m = s1.length;
  const n = s2.length;
  const dp = Array(m + 1).fill(0).map(() => Array(n + 1).fill(0));
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (s1[i - 1] === s2[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }
  return dp[m][n];
}

function updateReprocessedPage(pageNum, naturalText, tokenLogprobs, confidenceScore) {
  if (confidenceScore !== undefined && confidenceScore !== null) {
    pageConfidenceMap[pageNum] = confidenceScore;
    if (pageNum === previewPagesList[previewCurrentPage]) {
      updateConfidenceBadge(confidenceScore);
    }
  }

  if (tokenLogprobs) {
    activeJobTokenLogprobs[pageNum] = tokenLogprobs;
  }

  const isTranslated = DOM.txtMarkdownPreview.innerHTML.includes('TRANSLATED DOCUMENT');
  let block;
  if (isTranslated) {
    block = DOM.txtMarkdownPreview.querySelector('.ocr-page-block');
  } else {
    block = DOM.txtMarkdownPreview.querySelector(`.ocr-page-block[data-page="${pageNum}"]`);
  }

  if (block) {
    const contentDiv = block.querySelector('.ocr-page-content');
    if (contentDiv) {
      const currentText = contentDiv.innerText || contentDiv.textContent || '';
      const mergedText = mergeReprocessedText(currentText, naturalText);
      
      if (mergedText !== null) {
        contentDiv.innerHTML = escapeHtml(mergedText);
        forceSave();
      }
    }
  }
}

function setupZoningCanvas() {
  const canvas = DOM.zoneCanvas;
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const btnRerun = DOM.btnRerunZone;

  // Zoom and Pan updates helper
  function updateZoomTransform() {
    const wrapper = DOM.previewZoomWrapper;
    if (wrapper) {
      wrapper.style.transform = `translate(${panX}px, ${panY}px) scale(${zoomScale})`;
    }
    if (canvas) {
      canvas.style.cursor = (zoomScale > 1.0) ? 'grab' : 'crosshair';
    }
  }

  // Prevent browser context menu on canvas so right-click drag is smooth
  canvas.addEventListener('contextmenu', (e) => {
    e.preventDefault();
  });

  // Bind mouse wheel zoom on the canvas container
  const container = document.querySelector('.pdf-image-container');
  if (container) {
    container.addEventListener('wheel', (e) => {
      e.preventDefault();
      const zoomIntensity = 0.1;
      const oldScale = zoomScale;

      if (e.deltaY < 0) {
        zoomScale = Math.min(zoomScale + zoomIntensity, 5.0);
      } else {
        zoomScale = Math.max(zoomScale - zoomIntensity, 1.0);
      }

      if (zoomScale === 1.0) {
        panX = 0;
        panY = 0;
      } else {
        const rect = container.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;

        const factor = (zoomScale / oldScale) - 1;
        panX -= (mouseX - rect.width / 2 - panX) * factor;
        panY -= (mouseY - rect.height / 2 - panY) * factor;
      }

      updateZoomTransform();
    }, { passive: false });
  }

  // Keyboard modifiers for grab cursor
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Shift') {
      canvas.style.cursor = 'grab';
    }
  });

  window.addEventListener('keyup', (e) => {
    if (e.key === 'Shift') {
      canvas.style.cursor = (zoomScale > 1.0) ? 'grab' : 'crosshair';
    }
  });

  // Mousedown: either pan or draw zone
  canvas.addEventListener('mousedown', (e) => {
    // Pan mode triggers if shift is pressed, right/middle click, or if zoomed in (zoomScale > 1.0)
    if (e.shiftKey || e.button === 1 || e.button === 2 || zoomScale > 1.0) {
      isPanning = true;
      panStartX = e.clientX - panX;
      panStartY = e.clientY - panY;
      canvas.style.cursor = 'grabbing';
      if (e.button === 2) {
        e.preventDefault();
      }
      return;
    }

    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;

    zoneStartX = (e.clientX - rect.left) * scaleX;
    zoneStartY = (e.clientY - rect.top) * scaleY;
    zoneDrawing = true;
    zoneCoordinates = null;
    btnRerun.classList.add('is-hidden');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  });

  // Mousemove: either pan or update zone rectangle
  canvas.addEventListener('mousemove', (e) => {
    if (isPanning) {
      panX = e.clientX - panStartX;
      panY = e.clientY - panStartY;
      updateZoomTransform();
      return;
    }

    if (!zoneDrawing) return;

    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;

    const currentX = (e.clientX - rect.left) * scaleX;
    const currentY = (e.clientY - rect.top) * scaleY;

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    ctx.strokeStyle = '#ef4444';
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.fillStyle = 'rgba(239, 68, 68, 0.15)';
    
    const x = Math.min(zoneStartX, currentX);
    const y = Math.min(zoneStartY, currentY);
    const w = Math.abs(currentX - zoneStartX);
    const h = Math.abs(currentY - zoneStartY);

    ctx.fillRect(x, y, w, h);
    ctx.strokeRect(x, y, w, h);
  });

  // Mouseup: finalize pan or zone coordinates
  canvas.addEventListener('mouseup', (e) => {
    if (isPanning) {
      isPanning = false;
      canvas.style.cursor = (zoomScale > 1.0 || e.shiftKey) ? 'grab' : 'crosshair';
      return;
    }

    if (!zoneDrawing) return;
    zoneDrawing = false;

    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;

    const currentX = (e.clientX - rect.left) * scaleX;
    const currentY = (e.clientY - rect.top) * scaleY;

    const w = Math.abs(currentX - zoneStartX);
    const h = Math.abs(currentY - zoneStartY);

    if (w < 10 || h < 10) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      btnRerun.classList.add('is-hidden');
      zoneCoordinates = null;
      return;
    }

    const x = Math.min(zoneStartX, currentX);
    const y = Math.min(zoneStartY, currentY);

    zoneCoordinates = {
      x: x / canvas.width,
      y: y / canvas.height,
      width: w / canvas.width,
      height: h / canvas.height
    };

    btnRerun.classList.remove('is-hidden');
  });

  // Simple click outside zone clears drawing
  canvas.addEventListener('click', (e) => {
    if (e.shiftKey) return;
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    
    const currentX = (e.clientX - rect.left) * scaleX;
    const currentY = (e.clientY - rect.top) * scaleY;
    const w = Math.abs(currentX - zoneStartX);
    const h = Math.abs(currentY - zoneStartY);
    if (w < 5 && h < 5) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      btnRerun.classList.add('is-hidden');
      zoneCoordinates = null;
    }
  });

  // Zoom Button controls
  if (DOM.btnZoomIn) {
    DOM.btnZoomIn.addEventListener('click', () => {
      zoomScale = Math.min(zoomScale + 0.25, 5.0);
      updateZoomTransform();
    });
  }
  if (DOM.btnZoomOut) {
    DOM.btnZoomOut.addEventListener('click', () => {
      zoomScale = Math.max(zoomScale - 0.25, 1.0);
      if (zoomScale === 1.0) {
        panX = 0;
        panY = 0;
      }
      updateZoomTransform();
    });
  }
  if (DOM.btnZoomReset) {
    DOM.btnZoomReset.addEventListener('click', () => {
      zoomScale = 1.0;
      panX = 0;
      panY = 0;
      updateZoomTransform();
    });
  }

  // Markdown Preview zoom helper
  function updateMdZoom() {
    const preview = DOM.txtMarkdownPreview;
    if (preview) {
      preview.style.fontSize = `${mdZoomScale}rem`;
    }
  }

  // Bind markdown preview Ctrl + wheel zoom
  if (DOM.txtMarkdownPreview) {
    DOM.txtMarkdownPreview.addEventListener('wheel', (e) => {
      if (e.ctrlKey) {
        e.preventDefault();
        const zoomIntensity = 0.1;
        if (e.deltaY < 0) {
          mdZoomScale = Math.min(mdZoomScale + zoomIntensity, 2.5);
        } else {
          mdZoomScale = Math.max(mdZoomScale - zoomIntensity, 0.7);
        }
        updateMdZoom();
      }
    }, { passive: false });
  }

  // Bind markdown zoom buttons
  if (DOM.btnMdZoomIn) {
    DOM.btnMdZoomIn.addEventListener('click', () => {
      mdZoomScale = Math.min(mdZoomScale + 0.1, 2.5);
      updateMdZoom();
    });
  }
  if (DOM.btnMdZoomOut) {
    DOM.btnMdZoomOut.addEventListener('click', () => {
      mdZoomScale = Math.max(mdZoomScale - 0.1, 0.7);
      updateMdZoom();
    });
  }
  if (DOM.btnMdZoomReset) {
    DOM.btnMdZoomReset.addEventListener('click', () => {
      mdZoomScale = 1.0;
      updateMdZoom();
    });
  }

  btnRerun.addEventListener('click', async () => {
    if (!currentJobId || !zoneCoordinates) return;
    const pageNum = previewPagesList[previewCurrentPage];
    
    btnRerun.disabled = true;
    btnRerun.textContent = 'Processing...';

    try {
      const response = await fetch('/api/jobs/reprocess-zone', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          job_id: currentJobId,
          page_num: pageNum,
          x: zoneCoordinates.x,
          y: zoneCoordinates.y,
          width: zoneCoordinates.width,
          height: zoneCoordinates.height
        })
      });

      if (!response.ok) {
        throw new Error(await response.text());
      }

      const result = await response.json();
      
      let textToInsert = result.natural_text;
      const isTranslated = DOM.txtMarkdownPreview.innerHTML.includes('TRANSLATED DOCUMENT');
      if (isTranslated) {
        btnRerun.textContent = 'Translating...';
        try {
          const transRes = await fetch('/api/jobs/translate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: result.natural_text })
          });
          if (transRes.ok) {
            const transJson = await transRes.json();
            textToInsert = transJson.translated_text;
          }
        } catch (transErr) {
          console.error('Failed to translate reprocessed zone text:', transErr);
        }
      }
      
      updateReprocessedPage(result.page_num, textToInsert, isTranslated ? null : result.token_logprobs, result.confidence_score);
      
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      btnRerun.classList.add('is-hidden');
      zoneCoordinates = null;
      alert('Zone reprocessed successfully!');
    } catch (err) {
      alert(`Reprocessing zone failed: ${err.message}`);
    } finally {
      btnRerun.disabled = false;
      btnRerun.textContent = 'Re-Run Zone';
    }
  });
}

function setupQAPopover() {
  const popover = document.getElementById('qa-popover');
  if (!popover) return;
  const suggestionsList = document.getElementById('qa-suggestions-list');
  const inputCorrection = document.getElementById('input-qa-correction');
  const btnApply = document.getElementById('btn-qa-apply');

  DOM.txtMarkdownPreview.addEventListener('click', (e) => {
    const target = e.target;
    if (target.classList.contains('low-confidence')) {
      activeLowConfidenceSpan = target;
      
      const rect = target.getBoundingClientRect();
      const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
      const scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;

      popover.style.top = `${rect.bottom + scrollTop + 8}px`;
      popover.style.left = `${rect.left + scrollLeft}px`;
      popover.classList.remove('is-hidden');

      inputCorrection.value = target.textContent;
      inputCorrection.focus();
      inputCorrection.select();

      suggestionsList.innerHTML = '';
      const topChoicesRaw = target.getAttribute('data-top-choices');
      if (topChoicesRaw) {
        try {
          const choices = JSON.parse(decodeURIComponent(topChoicesRaw));
          if (choices && choices.length > 0) {
            choices.forEach(ch => {
              const btn = document.createElement('button');
              btn.className = 'qa-suggestion-badge';
              btn.textContent = ch.token;
              btn.title = `Confidence: ${ch.confidence}%`;
              btn.addEventListener('click', () => {
                target.textContent = ch.token;
                target.classList.remove('low-confidence');
                popover.classList.add('is-hidden');
                forceSave();
              });
              suggestionsList.appendChild(btn);
            });
          } else {
            suggestionsList.textContent = 'No suggestions';
          }
        } catch (err) {
          console.error('Failed to parse suggestions:', err);
        }
      }
    } else {
      popover.classList.add('is-hidden');
    }
  });

  btnApply.addEventListener('click', () => {
    if (activeLowConfidenceSpan) {
      activeLowConfidenceSpan.textContent = inputCorrection.value;
      activeLowConfidenceSpan.classList.remove('low-confidence');
      popover.classList.add('is-hidden');
      forceSave();
    }
  });

  inputCorrection.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      btnApply.click();
    }
  });

  document.addEventListener('mousedown', (e) => {
    if (!popover.contains(e.target) && !e.target.classList.contains('low-confidence')) {
      popover.classList.add('is-hidden');
    }
  });
}
