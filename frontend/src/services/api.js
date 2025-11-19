import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001';

// Create axios instance with default config
const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 60000, // 60 seconds default
});

// Response interceptor for error handling
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    console.error('API Error:', error);
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
  uploadPDF: async (file, onProgress) => {
    const formData = new FormData();
    formData.append('file', file);

    const response = await apiClient.post('/upload', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: 120000, // 2 minutes for large PDF uploads
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
      timeout: 5000, // 5 seconds for quick status checks
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
   * Check API health
   * @returns {Promise} Health status
   */
  checkHealth: async () => {
    const response = await axios.get('http://localhost:8001/health');
    return response.data;
  },
};

export default examAPI;
