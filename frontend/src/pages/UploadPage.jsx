import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Sparkles } from 'lucide-react';
import FileUpload from '../components/FileUpload';
import { Alert } from '../components/common';
import examAPI from '../services/api';

/**
 * UploadPage
 * Main page for uploading exam PDFs
 */
const UploadPage = () => {
  const navigate = useNavigate();
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [error, setError] = useState('');

  const handleFileSelect = (file) => {
    setSelectedFile(file);
    setError('');
  };

  const handleUpload = async (file) => {
    if (!file) return;

    setUploading(true);
    setError('');
    setUploadProgress(0);

    try {
      const response = await examAPI.uploadPDF(file, (progress) => {
        setUploadProgress(progress);
      });

      // Navigate to tracking page with submission ID
      navigate(`/track/${response.submission_id}`);
    } catch (err) {
      console.error('Upload failed:', err);
      setError(err.response?.data?.detail || 'Upload failed. Please try again.');
      setUploading(false);
      setUploadProgress(0);
    }
  };

  return (
    <div className="min-h-screen py-12 px-4">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="text-center mb-12">
          <div className="flex items-center justify-center gap-3 mb-4">
            <div className="w-12 h-12 bg-gradient-to-br from-blue-600 to-purple-600 rounded-xl flex items-center justify-center">
              <Sparkles className="w-7 h-7 text-white" />
            </div>
            <h1 className="text-4xl font-bold bg-gradient-to-r from-blue-600 to-purple-600 bg-clip-text text-transparent">
              Exam Answer Extractor
            </h1>
          </div>
          <p className="text-xl text-slate-600 max-w-2xl mx-auto mb-6">
            Upload your PDF exam answer sheet and let AI extract all answers automatically
          </p>
          
          <div className="flex items-center justify-center gap-6 mt-6 text-sm text-slate-500">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 bg-green-500 rounded-full"></div>
              <span>AI-Powered</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 bg-blue-500 rounded-full"></div>
              <span>Fast Processing</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 bg-purple-500 rounded-full"></div>
              <span>Accurate Results</span>
            </div>
          </div>
        </div>

        {/* Error Alert */}
        {error && (
          <div className="mb-6">
            <Alert type="error">{error}</Alert>
          </div>
        )}

        {/* File Upload */}
        <FileUpload
          onFileSelect={handleFileSelect}
          onUpload={handleUpload}
          uploading={uploading}
          uploadProgress={uploadProgress}
        />

        {/* Features */}
        <div className="mt-16 grid md:grid-cols-3 gap-6">
          <div className="text-center p-6">
            <div className="w-12 h-12 bg-blue-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <span className="text-2xl">🎯</span>
            </div>
            <h3 className="font-semibold text-slate-800 mb-2">Multiple Choice</h3>
            <p className="text-sm text-slate-600">
              Automatically detects and extracts all MCQ answers (A, B, C, D, E)
            </p>
          </div>

          <div className="text-center p-6">
            <div className="w-12 h-12 bg-purple-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <span className="text-2xl">✍️</span>
            </div>
            <h3 className="font-semibold text-slate-800 mb-2">Free Response</h3>
            <p className="text-sm text-slate-600">
              Extracts full written answers with complete text preservation
            </p>
          </div>

          <div className="text-center p-6">
            <div className="w-12 h-12 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <span className="text-2xl">💾</span>
            </div>
            <h3 className="font-semibold text-slate-800 mb-2">Export Results</h3>
            <p className="text-sm text-slate-600">
              Download results as JSON or view them directly in the browser
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default UploadPage;
