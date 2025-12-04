import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Sparkles, Code } from 'lucide-react';

const Navbar = () => {
  const location = useLocation();

  return (
    <nav className="bg-white/80 backdrop-blur-md border-b border-slate-200 sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16 items-center">
          {/* Logo */}
          <Link to="/" className="flex items-center gap-2">
            <div className="w-8 h-8 bg-gradient-to-br from-blue-600 to-purple-600 rounded-lg flex items-center justify-center">
              <Sparkles className="w-5 h-5 text-white" />
            </div>
            <span className="font-bold text-xl bg-gradient-to-r from-blue-600 to-purple-600 bg-clip-text text-transparent">
              ExamExtractor
            </span>
          </Link>

          {/* Navigation Links */}
          <div className="flex items-center gap-6">
            <Link 
              to="/" 
              className={`text-sm font-medium transition-colors ${
                location.pathname === '/' ? 'text-blue-600' : 'text-slate-600 hover:text-blue-600'
              }`}
            >
              Upload
            </Link>
            <Link 
              to="/api-docs" 
              className={`text-sm font-medium transition-colors flex items-center gap-1 ${
                location.pathname === '/api-docs' ? 'text-blue-600' : 'text-slate-600 hover:text-blue-600'
              }`}
            >
              <Code className="w-4 h-4" />
              API Docs
            </Link>
          </div>
        </div>
      </div>
    </nav>
  );
};

export default Navbar;
