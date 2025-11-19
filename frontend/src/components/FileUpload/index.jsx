import React, { useState, useCallback } from 'react';
import { Upload, FileText, X, CheckCircle2 } from 'lucide-react';
import { Button, Alert, ProgressBar } from '../common';
import clsx from 'clsx';

/**
 * FileUpload Component
 * Drag-and-drop file upload with preview and progress
 */
const FileUpload = ({ onFileSelect, onUpload, uploading = false, uploadProgress = 0 }) => {
  const [selectedFile, setSelectedFile] = useState(null);
  const [dragActive, setDragActive] = useState(false);
  const [error, setError] = useState('');

  const handleDrag = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  }, []);

  const validateFile = (file) => {
    setError('');
    
    if (!file) {
      setError('No file selected');
      return false;
    }

    if (file.type !== 'application/pdf') {
      setError('Only PDF files are allowed');
      return false;
    }

    const maxSize = 50 * 1024 * 1024; // 50MB
    if (file.size > maxSize) {
      setError('File size must be less than 50MB');
      return false;
    }

    return true;
  };

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const file = e.dataTransfer.files[0];
      if (validateFile(file)) {
        setSelectedFile(file);
        onFileSelect(file);
      }
    }
  }, [onFileSelect]);

  const handleChange = (e) => {
    e.preventDefault();
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      if (validateFile(file)) {
        setSelectedFile(file);
        onFileSelect(file);
      }
    }
  };

  const handleRemove = () => {
    setSelectedFile(null);
    setError('');
    onFileSelect(null);
  };

  const handleUploadClick = () => {
    if (selectedFile && onUpload) {
      onUpload(selectedFile);
    }
  };

  return (
    <div className="w-full max-w-2xl mx-auto">
      {error && (
        <Alert type="error" className="mb-4">
          {error}
        </Alert>
      )}

      {!selectedFile ? (
        <div
          className={clsx(
            'relative border-3 border-dashed rounded-xl p-12 transition-all duration-200',
            dragActive 
              ? 'border-blue-500 bg-blue-50' 
              : 'border-slate-300 bg-white hover:border-blue-400 hover:bg-slate-50'
          )}
          onDragEnter={handleDrag}
          onDragLeave={handleDrag}
          onDragOver={handleDrag}
          onDrop={handleDrop}
        >
          <input
            type="file"
            accept=".pdf"
            onChange={handleChange}
            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
            disabled={uploading}
          />
          
          <div className="flex flex-col items-center justify-center gap-4 text-center">
            <div className="w-20 h-20 bg-blue-100 rounded-full flex items-center justify-center">
              <Upload className="w-10 h-10 text-blue-600" />
            </div>
            
            <div>
              <h3 className="text-xl font-semibold text-slate-800 mb-2">
                Upload Exam Answer Sheet
              </h3>
              <p className="text-slate-600">
                Drag and drop your PDF file here, or click to browse
              </p>
              <p className="text-sm text-slate-500 mt-2">
                Maximum file size: 50MB
              </p>
            </div>
            
            <Button variant="secondary" disabled={uploading}>
              Browse Files
            </Button>
          </div>
        </div>
      ) : (
        <div className="bg-white rounded-xl border-2 border-blue-200 p-6">
          <div className="flex items-start justify-between mb-4">
            <div className="flex items-center gap-4 flex-1">
              <div className="w-12 h-12 bg-blue-100 rounded-lg flex items-center justify-center flex-shrink-0">
                <FileText className="w-6 h-6 text-blue-600" />
              </div>
              
              <div className="flex-1 min-w-0">
                <h4 className="font-semibold text-slate-800 truncate">
                  {selectedFile.name}
                </h4>
                <p className="text-sm text-slate-500">
                  {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
                </p>
              </div>
            </div>
            
            {!uploading && (
              <button
                onClick={handleRemove}
                className="text-slate-400 hover:text-red-600 transition-colors p-1"
              >
                <X className="w-5 h-5" />
              </button>
            )}
          </div>

          {uploading && (
            <div className="mb-4">
              <div className="flex justify-between text-sm text-slate-600 mb-2">
                <span>Uploading...</span>
                <span>{uploadProgress}%</span>
              </div>
              <ProgressBar progress={uploadProgress} />
            </div>
          )}

          {!uploading && (
            <Button 
              variant="primary" 
              onClick={handleUploadClick}
              className="w-full"
            >
              <Upload className="w-5 h-5" />
              Start Extraction
            </Button>
          )}
        </div>
      )}
    </div>
  );
};

export default FileUpload;
