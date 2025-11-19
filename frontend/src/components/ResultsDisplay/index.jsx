import React from 'react';
import { CheckSquare, FileText, Download, Calendar } from 'lucide-react';
import { Card, Badge, Button } from '../common';

/**
 * ResultsDisplay Component
 * Shows extracted MCQ and free response answers
 */
const ResultsDisplay = ({ results }) => {
  if (!results) return null;

  const { filename, multiple_choice = [], free_response = [], created_at, processed_at } = results;

  const handleExportJSON = () => {
    const dataStr = JSON.stringify(results, null, 2);
    const dataUri = 'data:application/json;charset=utf-8,' + encodeURIComponent(dataStr);
    const exportFileDefaultName = `${filename.replace('.pdf', '')}_results.json`;
    
    const linkElement = document.createElement('a');
    linkElement.setAttribute('href', dataUri);
    linkElement.setAttribute('download', exportFileDefaultName);
    linkElement.click();
  };

  const formatDate = (dateString) => {
    return new Date(dateString).toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header Card */}
      <Card>
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="flex-1">
            <h2 className="text-2xl font-bold text-slate-800 mb-2">Extraction Results</h2>
            <p className="text-slate-600">{filename}</p>
            {processed_at && (
              <div className="flex items-center gap-2 text-sm text-slate-500 mt-2">
                <Calendar className="w-4 h-4" />
                <span>Processed: {formatDate(processed_at)}</span>
              </div>
            )}
          </div>
          
          <div className="flex flex-col sm:flex-row gap-3">
            <Button variant="secondary" onClick={handleExportJSON}>
              <Download className="w-5 h-5" />
              Export JSON
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mt-6">
          <div className="bg-blue-50 rounded-lg p-4">
            <p className="text-sm text-blue-600 mb-1">Total Answers</p>
            <p className="text-3xl font-bold text-blue-700">
              {multiple_choice.length + free_response.length}
            </p>
          </div>
          <div className="bg-green-50 rounded-lg p-4">
            <p className="text-sm text-green-600 mb-1">Multiple Choice</p>
            <p className="text-3xl font-bold text-green-700">{multiple_choice.length}</p>
          </div>
          <div className="bg-purple-50 rounded-lg p-4">
            <p className="text-sm text-purple-600 mb-1">Free Response</p>
            <p className="text-3xl font-bold text-purple-700">{free_response.length}</p>
          </div>
        </div>
      </Card>

      {/* Multiple Choice Answers */}
      {multiple_choice.length > 0 && (
        <Card>
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
              <CheckSquare className="w-6 h-6 text-green-600" />
            </div>
            <h3 className="text-xl font-bold text-slate-800">
              Multiple Choice Answers ({multiple_choice.length})
            </h3>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
            {multiple_choice.map((mcq) => (
              <div
                key={mcq.question}
                className="bg-gradient-to-br from-slate-50 to-slate-100 rounded-lg p-4 border border-slate-200 hover:shadow-md transition-shadow"
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-600">Q{mcq.question}</span>
                  <Badge variant="success">{mcq.answer}</Badge>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Free Response Answers */}
      {free_response.length > 0 && (
        <Card>
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center">
              <FileText className="w-6 h-6 text-purple-600" />
            </div>
            <h3 className="text-xl font-bold text-slate-800">
              Free Response Answers ({free_response.length})
            </h3>
          </div>

          <div className="space-y-4">
            {free_response.map((fr) => (
              <div
                key={fr.question}
                className="bg-gradient-to-br from-purple-50 to-purple-100/50 rounded-lg p-5 border border-purple-200"
              >
                <div className="flex items-start gap-4">
                  <Badge variant="info" className="flex-shrink-0">
                    Q{fr.question}
                  </Badge>
                  <div className="flex-1">
                    <p className="text-slate-700 leading-relaxed whitespace-pre-wrap">
                      {fr.response}
                    </p>
                    <p className="text-sm text-slate-500 mt-2">
                      {fr.response.split(' ').length} words
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Empty State */}
      {multiple_choice.length === 0 && free_response.length === 0 && (
        <Card className="text-center py-12">
          <div className="text-slate-400 mb-4">
            <FileText className="w-16 h-16 mx-auto" />
          </div>
          <h3 className="text-xl font-semibold text-slate-600 mb-2">No Answers Found</h3>
          <p className="text-slate-500">
            No answers were extracted from this exam sheet.
          </p>
        </Card>
      )}
    </div>
  );
};

export default ResultsDisplay;
