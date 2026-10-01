import { Routes, Route } from 'react-router-dom';
import Navbar from './components/common/Navbar';
import UploadPage from './pages/UploadPage';
import TrackingPage from './pages/TrackingPage';
import ApiDocsPage from './pages/ApiDocsPage';
import ApiAccess from './components/common/ApiAccess';

function App() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-purple-50">
      <Navbar />
      
      <div className="pt-4">
        <Routes>
          <Route path="/" element={<ApiAccess><UploadPage /></ApiAccess>} />
          <Route path="/track/:submissionId" element={<ApiAccess><TrackingPage /></ApiAccess>} />
          <Route path="/api-docs" element={<ApiDocsPage />} />
        </Routes>
      </div>
    </div>
  );
}

export default App;
