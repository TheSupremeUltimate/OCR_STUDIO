/**
 * OCR Studio - API Client
 * Encapsulates all REST HTTP communications with the FastAPI backend.
 */

const BASE_URL = '/api';

/**
 * Helper to handle fetch responses and error logging
 */
async function handleResponse(response) {
  if (!response.ok) {
    let errorDetail = response.statusText;
    try {
      const errJson = await response.json();
      errorDetail = errJson.detail || errorDetail;
    } catch (e) {
      // response might not be JSON
    }
    throw new Error(errorDetail);
  }
  return response.json();
}

/**
 * Ping backend to check health and connection to LM Studio server
 * GET /api/health
 */
export async function checkHealth() {
  const res = await fetch(`${BASE_URL}/health`);
  return handleResponse(res);
}

/**
 * Fetch current application settings
 * GET /api/settings
 */
export async function getSettings() {
  const res = await fetch(`${BASE_URL}/settings`);
  return handleResponse(res);
}

/**
 * Update application settings
 * PUT /api/settings
 */
export async function updateSettings(settings) {
  const res = await fetch(`${BASE_URL}/settings`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(settings),
  });
  return handleResponse(res);
}

/**
 * Upload a PDF file
 * POST /api/upload
 */
export async function uploadPdf(file) {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(`${BASE_URL}/upload`, {
    method: 'POST',
    body: formData,
  });
  return handleResponse(res);
}

/**
 * Start a new OCR job
 * POST /api/jobs
 */
export async function startJob(config) {
  const res = await fetch(`${BASE_URL}/jobs`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(config),
  });
  return handleResponse(res);
}

/**
 * List recent jobs
 * GET /api/jobs
 */
export async function getRecentJobs() {
  const res = await fetch(`${BASE_URL}/jobs`);
  return handleResponse(res);
}

/**
 * Trigger browser file download for a completed Markdown result
 * GET /api/download/{filename}
 */
export function downloadResult(filename, format = null) {
  if (!filename) {
    console.error("Filename is required for download.");
    return;
  }
  // Programmatically trigger download
  const link = document.createElement('a');
  let url = `${BASE_URL}/download/${encodeURIComponent(filename)}`;
  if (format) {
    url += `?fmt=${encodeURIComponent(format)}`;
  }
  link.href = url;
  
  // Set default download file name, correcting suffix if needed
  let downloadName = filename;
  if (format) {
    downloadName = filename.replace(/\.md$/, `.${format}`);
  }
  link.download = downloadName;
  
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

/**
 * Cancel a running job
 * POST /api/jobs/{jobId}/cancel
 */
export async function cancelJob(jobId) {
  const res = await fetch(`${BASE_URL}/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST'
  });
  return handleResponse(res);
}

/**
 * Clear recent jobs history
 * DELETE /api/jobs
 */
export async function clearJobs() {
  const res = await fetch(`${BASE_URL}/jobs`, {
    method: 'DELETE'
  });
  return handleResponse(res);
}

/**
 * Open log file in default system editor
 * POST /api/logs/open
 */
export async function openLogs() {
  const res = await fetch(`${BASE_URL}/logs/open`, {
    method: 'POST'
  });
  return handleResponse(res);
}

/**
 * Fetch available models from LM Studio
 * GET /api/models
 */
export async function getModels() {
  const res = await fetch(`${BASE_URL}/models`);
  return handleResponse(res);
}

/**
 * Fetch available glossary preset names
 * GET /api/glossaries
 */
export async function getGlossaries() {
  const res = await fetch(`${BASE_URL}/glossaries`);
  return handleResponse(res);
}

/**
 * Fetch a single glossary preset's injectable terms + raw annotated text
 * GET /api/glossaries/{name}
 */
export async function getGlossary(name) {
  const res = await fetch(`${BASE_URL}/glossaries/${encodeURIComponent(name)}`);
  return handleResponse(res);
}

