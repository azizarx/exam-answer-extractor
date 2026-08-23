
import axios from 'axios';

// Prefer VITE_API_BASE_URL when set (build-time).
// Production default: same-origin (""), so nginx can proxy /upload, /health, etc.
// Local Vite default: http://localhost:8000
const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ??
  (import.meta.env.DEV ? 'http://localhost:8000' : '');

// Wall-clock ceiling for one request. axios counts upload time against this,
// so anything carrying a PDF needs room for the body to leave the browser: a
// 100 MB scan on a 1 Mbps uplink is ~13 minutes before the server even replies.
// nginx already allows 600s body / 1g bodies, so the client was the only wall.
const TIMEOUT = {
  poll: 30 * 1000,        // status polls — short so the UI stays responsive
  standard: 2 * 60 * 1000, // ordinary JSON calls
  transfer: 10 * 60 * 1000, // large payloads in either direction
  marking: 15 * 60 * 1000, // synchronous marking of a whole submission
  upload: 30 * 60 * 1000,  // multipart PDF upload
};

// Create axios instance with default config
const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: TIMEOUT.standard,
});

const decodeFilename = (value) => {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
};

const filenameFromDisposition = (disposition, fallback) => {
  if (!disposition) return fallback;

  const encodedMatch = disposition.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
  if (encodedMatch?.[1]) {
    return decodeFilename(encodedMatch[1].trim().replace(/^["']|["']$/g, ''));
  }

  const plainMatch = disposition.match(/filename\s*=\s*"([^"]+)"|filename\s*=\s*([^;]+)/i);
  return (plainMatch?.[1] || plainMatch?.[2] || fallback).trim();
};

// Response interceptor for error handling
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const requestUrl = String(error?.config?.url || '');
    const isPollingEndpoint = requestUrl.includes('/status/') || requestUrl.includes('/logs');
    const isTimeout = error?.code === 'ECONNABORTED';

    if (!(isTimeout && isPollingEndpoint)) {
      console.error('API Error:', error);
    }
    return Promise.reject(error);
  }
);

/**
 * API service for interacting with the exam extraction backend
 */
export const examAPI = {
  /**
   * Upload a PDF file for processing
   * @param {File} file - PDF file to upload
   * @param {Function} onProgress - Progress callback (optional)
   * @returns {Promise} Upload response with submission_id
   */
  /**
   * Get all exam layout templates (including per-paper variants).
   * @returns {Promise} List of template objects
   */
  getTemplates: async () => {
    const response = await apiClient.get('/templates/all');
    return response.data;
  },

  uploadPDF: async (file, onProgress, templateId) => {
    const formData = new FormData();
    formData.append('file', file);

    const params = templateId ? `?template_id=${encodeURIComponent(templateId)}` : '';
    const response = await apiClient.post(`/upload${params}`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: TIMEOUT.upload,
      onUploadProgress: (progressEvent) => {
        if (onProgress && progressEvent.total) {
          const percentCompleted = Math.round(
            (progressEvent.loaded * 100) / progressEvent.total
          );
          onProgress(percentCompleted);
        }
      },
    });

    return response.data;
  },

  /**
   * Check the processing status of a submission
   * @param {number} submissionId - ID of the submission
   * @returns {Promise} Status information
   */
  getStatus: async (submissionId) => {
    const response = await apiClient.get(`/status/${submissionId}`, {
      timeout: TIMEOUT.poll, // tolerate DB contention while extraction writes are in progress
    });
    return response.data;
  },

  /**
   * Get the extracted results for a completed submission
   * @param {number} submissionId - ID of the submission
   * @returns {Promise} Extraction results
   */
  getSubmission: async (submissionId) => {
    const response = await apiClient.get(`/submission/${submissionId}`);
    return response.data;
  },

  /**
   * Download the actual structured JSON for a submission
   * @param {number} submissionId - ID of the submission
   * @returns {Promise} JSON object (structured)
   */
  downloadSubmissionJSON: async (submissionId) => {
    const response = await apiClient.get(`/submission/${submissionId}/json`, {
      responseType: 'json',
      timeout: TIMEOUT.transfer, // allow more time for large files
    });
    return response.data;
  },

  /**
   * Get the latest marking run, candidate outcomes, and run history.
   * @param {number} submissionId - ID of the submission
   * @returns {Promise} Marking detail
   */
  getSubmissionMarking: async (submissionId) => {
    const response = await apiClient.get(`/submission/${submissionId}/marking`);
    return response.data;
  },

  /**
   * Run marking again without rerunning extraction.
   * @param {number} submissionId - ID of the submission
   * @param {number|null} answerKeyId - Optional immutable answer-key version
   * @returns {Promise} Completed or terminal marking run
   */
  markSubmission: async (submissionId, answerKeyId = null) => {
    const body = answerKeyId == null ? {} : { answer_key_id: answerKeyId };
    const response = await apiClient.post(`/submission/${submissionId}/mark`, body, {
      // Marking is synchronous in the request and now spends one extra vision
      // call per candidate that has diagram questions.
      timeout: TIMEOUT.marking,
    });
    return response.data;
  },

  /**
   * Confirm extraction answers that were flagged needs_review.
   * Call markSubmission afterwards to refresh scores.
   */
  confirmCandidateReview: async (submissionId, candidateId, payload) => {
    const response = await apiClient.post(
      `/submission/${submissionId}/candidates/${candidateId}/confirm-review`,
      payload,
      { timeout: TIMEOUT.standard },
    );
    return response.data;
  },

  /**
   * Download the current marked JSON payload.
   * @param {number} submissionId - ID of the submission
   * @param {string} fallbackName - Filename used if the server omits a name
   * @returns {Promise<{blob: Blob, filename: string}>}
   */
  getMarkedJSONDownload: async (submissionId, fallbackName = 'results.marked.json') => {
    const response = await apiClient.get(`/submission/${submissionId}/marked-json`, {
      responseType: 'blob',
      timeout: TIMEOUT.transfer,
    });
    return {
      blob: response.data,
      filename: filenameFromDisposition(
        response.headers['content-disposition'],
        fallbackName
      ),
    };
  },

  /**
   * Fetch the candidate's scanned page as an object URL.
   *
   * Fetched through axios rather than set as an <img src> so the API key
   * header still travels when API_KEY is configured; the caller must
   * URL.revokeObjectURL the result when it is done with it.
   * @param {number} submissionId
   * @param {number} pageNumber - 1-based page of the uploaded PDF
   * @returns {Promise<string>} object URL for an image/png
   */
  getPageImageURL: async (submissionId, pageNumber) => {
    const response = await apiClient.get(
      `/submission/${submissionId}/page/${pageNumber}.png`,
      { responseType: 'blob', timeout: TIMEOUT.transfer },
    );
    return URL.createObjectURL(response.data);
  },

  /**
   * Fetch a candidate's stored crop for one diagram question.
   * @returns {Promise<string>} object URL for an image/png
   */
  getDiagramCropURL: async (submissionId, candidateId, question) => {
    const response = await apiClient.get(
      `/submission/${submissionId}/candidates/${candidateId}/diagram/${question}.png`,
      { responseType: 'blob', timeout: TIMEOUT.standard },
    );
    return URL.createObjectURL(response.data);
  },

  /**
   * Fetch the answer key's reference drawing for one diagram question.
   * @returns {Promise<string>} object URL for an image/png
   */
  getReferenceDiagramURL: async (templateId, question) => {
    const response = await apiClient.get(
      `/answer-keys/reference/${templateId}/${question}.png`,
      { responseType: 'blob', timeout: TIMEOUT.standard },
    );
    return URL.createObjectURL(response.data);
  },

  /**
   * List all submissions with optional filtering
   * @param {Object} params - Query parameters
   * @returns {Promise} List of submissions
   */
  listSubmissions: async (params = {}) => {
    const response = await apiClient.get('/submissions', { params });
    return response.data;
  },

  /**
   * Delete a submission
   * @param {number} submissionId - ID of the submission to delete
   * @returns {Promise} Deletion confirmation
   */
  deleteSubmission: async (submissionId) => {
    const response = await apiClient.delete(`/submission/${submissionId}`);
    return response.data;
  },

  /**
   * Get recent processing logs for a submission
   * @param {number} submissionId
   * @param {number} limit
   */
  getSubmissionLogs: async (submissionId, limit = 50) => {
    const response = await apiClient.get(`/submission/${submissionId}/logs`, {
      params: { limit },
      timeout: TIMEOUT.poll,
    });
    return response.data;
  },

  /**
   * Check API health
   * @returns {Promise} Health status
   */
  checkHealth: async () => {
    const response = await apiClient.get('/health');
    return response.data;
  },
};

export default examAPI;
