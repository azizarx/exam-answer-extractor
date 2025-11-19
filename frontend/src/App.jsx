import React from 'react';
import { Routes, Route } from 'react-router-dom';
import UploadPage from './pages/UploadPage';
import TrackingPage from './pages/TrackingPage';

function App() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-purple-50">
      <Routes>
        <Route path="/" element={<UploadPage />} />
        <Route path="/track/:submissionId" element={<TrackingPage />} />
      </Routes>
    </div>
  );
}

export default App;
