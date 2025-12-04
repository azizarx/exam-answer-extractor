import React from 'react';
import { ArrowLeft, Code, Copy, Check, Zap, Upload, FileJson } from 'lucide-react';
import { Link } from 'react-router-dom';

const ApiDocsPage = () => {
  const [copiedIndex, setCopiedIndex] = React.useState(null);

  const copyCode = (code, index) => {
    navigator.clipboard.writeText(code);
    setCopiedIndex(index);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  return (
    <div className="min-h-screen py-12 px-4">
      <div className="max-w-4xl mx-auto">
        <div className="mb-8">
          <Link to="/" className="inline-flex items-center text-blue-600 hover:text-blue-800">
            <ArrowLeft className="w-4 h-4 mr-2" />
            Back to Upload
          </Link>
        </div>

        <div className="bg-white rounded-2xl shadow-xl p-8">
          <div className="flex items-center gap-3 mb-6">
            <div className="p-3 bg-blue-100 rounded-xl">
              <Code className="w-8 h-8 text-blue-600" />
            </div>
            <h1 className="text-3xl font-bold text-slate-800">API Documentation</h1>
          </div>

          <div className="prose max-w-none">
            <p className="text-lg text-slate-600 mb-8">
              Integrate our AI-powered exam extraction engine into your applications. Our API uses advanced multi-threading for fast, parallel processing of multi-page documents.
            </p>

            {/* Features */}
            <div className="grid md:grid-cols-3 gap-4 mb-10">
              <div className="bg-gradient-to-br from-blue-50 to-blue-100 p-4 rounded-lg">
                <Zap className="w-8 h-8 text-blue-600 mb-2" />
                <h3 className="font-bold text-slate-800">Fast Processing</h3>
                <p className="text-sm text-slate-600">Multi-threaded extraction for 2-3x speed improvement</p>
              </div>
              <div className="bg-gradient-to-br from-purple-50 to-purple-100 p-4 rounded-lg">
                <FileJson className="w-8 h-8 text-purple-600 mb-2" />
                <h3 className="font-bold text-slate-800">Structured JSON</h3>
                <p className="text-sm text-slate-600">Clean, consistent JSON format ready to use</p>
              </div>
              <div className="bg-gradient-to-br from-green-50 to-green-100 p-4 rounded-lg">
                <Upload className="w-8 h-8 text-green-600 mb-2" />
                <h3 className="font-bold text-slate-800">Simple Integration</h3>
                <p className="text-sm text-slate-600">RESTful API with standard multipart uploads</p>
              </div>
            </div>

            {/* Quick Start */}
            <div className="mb-10">
              <h2 className="text-2xl font-bold text-slate-800 mb-4">Quick Start</h2>
              <div className="flex items-center gap-2 mb-4">
                <span className="px-3 py-1 bg-green-100 text-green-700 font-mono font-bold rounded">POST</span>
                <code className="text-slate-700 font-mono">/extract/json</code>
              </div>
              
              <p className="text-slate-600 mb-4">
                Upload a PDF exam answer sheet and receive extracted data immediately. No database storage, no background jobs - just instant results.
              </p>

              <div className="bg-slate-900 rounded-lg p-4 relative group mb-2">
                <button 
                  onClick={() => copyCode(`curl -X POST "http://localhost:8000/extract/json" \\
  -H "accept: application/json" \\
  -H "Content-Type: multipart/form-data" \\
  -F "file=@exam.pdf"`, 0)}
                  className="absolute top-4 right-4 p-2 text-slate-400 hover:text-white bg-slate-800 rounded transition-colors"
                  title="Copy to clipboard"
                >
                  {copiedIndex === 0 ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                </button>
                <pre className="text-blue-300 font-mono text-sm overflow-x-auto">
{`curl -X POST "http://localhost:8000/extract/json" \\
  -H "accept: application/json" \\
  -H "Content-Type: multipart/form-data" \\
  -F "file=@exam.pdf"`}
                </pre>
              </div>
            </div>

            {/* Python Example */}
            <div className="mb-10">
              <h2 className="text-2xl font-bold text-slate-800 mb-4">Usage Examples</h2>
              
              <h3 className="text-lg font-semibold text-slate-700 mb-3">Python (requests)</h3>
              <div className="bg-slate-900 rounded-lg p-4 relative group mb-6">
                <button 
                  onClick={() => copyCode(`import requests

url = "http://localhost:8000/extract/json"

# Open and upload PDF file
with open("exam.pdf", "rb") as pdf_file:
    files = {"file": ("exam.pdf", pdf_file, "application/pdf")}
    response = requests.post(url, files=files)

# Parse JSON response
if response.status_code == 200:
    data = response.json()
    print(f"Extracted {data['total_multiple_choice']} MCQ answers")
    print(f"Extracted {data['total_free_response']} free responses")
    
    # Access answers
    for mcq in data["multiple_choice"]:
        print(f"Q{mcq['question']}: {mcq['answer']}")
else:
    print(f"Error: {response.status_code}")`, 1)}
                  className="absolute top-4 right-4 p-2 text-slate-400 hover:text-white bg-slate-800 rounded transition-colors"
                  title="Copy to clipboard"
                >
                  {copiedIndex === 1 ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                </button>
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">
{`import requests

url = "http://localhost:8000/extract/json"

# Open and upload PDF file
with open("exam.pdf", "rb") as pdf_file:
    files = {"file": ("exam.pdf", pdf_file, "application/pdf")}
    response = requests.post(url, files=files)

# Parse JSON response
if response.status_code == 200:
    data = response.json()
    print(f"Extracted {data['total_multiple_choice']} MCQ answers")
    print(f"Extracted {data['total_free_response']} free responses")
    
    # Access answers
    for mcq in data["multiple_choice"]:
        print(f"Q{mcq['question']}: {mcq['answer']}")
else:
    print(f"Error: {response.status_code}")`}
                </pre>
              </div>

              <h3 className="text-lg font-semibold text-slate-700 mb-3">JavaScript (fetch)</h3>
              <div className="bg-slate-900 rounded-lg p-4 relative group mb-6">
                <button 
                  onClick={() => copyCode(`const formData = new FormData();
formData.append('file', pdfFile); // pdfFile is a File object

fetch('http://localhost:8000/extract/json', {
  method: 'POST',
  body: formData
})
  .then(response => response.json())
  .then(data => {
    console.log(\`Extracted \${data.total_multiple_choice} MCQ answers\`);
    console.log(\`Extracted \${data.total_free_response} free responses\`);
    
    // Process MCQ answers
    data.multiple_choice.forEach(mcq => {
      console.log(\`Q\${mcq.question}: \${mcq.answer}\`);
    });
    
    // Process free response answers
    data.free_response.forEach(fr => {
      console.log(\`Q\${fr.question}: \${fr.response}\`);
    });
  })
  .catch(error => console.error('Error:', error));`, 2)}
                  className="absolute top-4 right-4 p-2 text-slate-400 hover:text-white bg-slate-800 rounded transition-colors"
                  title="Copy to clipboard"
                >
                  {copiedIndex === 2 ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                </button>
                <pre className="text-yellow-300 font-mono text-sm overflow-x-auto">
{`const formData = new FormData();
formData.append('file', pdfFile); // pdfFile is a File object

fetch('http://localhost:8000/extract/json', {
  method: 'POST',
  body: formData
})
  .then(response => response.json())
  .then(data => {
    console.log(\`Extracted \${data.total_multiple_choice} MCQ answers\`);
    console.log(\`Extracted \${data.total_free_response} free responses\`);
    
    // Process MCQ answers
    data.multiple_choice.forEach(mcq => {
      console.log(\`Q\${mcq.question}: \${mcq.answer}\`);
    });
    
    // Process free response answers
    data.free_response.forEach(fr => {
      console.log(\`Q\${fr.question}: \${fr.response}\`);
    });
  })
  .catch(error => console.error('Error:', error));`}
                </pre>
              </div>

              <h3 className="text-lg font-semibold text-slate-700 mb-3">Node.js (axios)</h3>
              <div className="bg-slate-900 rounded-lg p-4 relative group mb-6">
                <button 
                  onClick={() => copyCode(`const axios = require('axios');
const FormData = require('form-data');
const fs = require('fs');

const form = new FormData();
form.append('file', fs.createReadStream('exam.pdf'));

axios.post('http://localhost:8000/extract/json', form, {
  headers: form.getHeaders()
})
  .then(response => {
    const data = response.data;
    console.log(\`Processing time: \${data.processing_time}s\`);
    console.log(\`Pages processed: \${data.pages_processed}\`);
    
    // Save to file
    fs.writeFileSync('results.json', JSON.stringify(data, null, 2));
    console.log('Results saved to results.json');
  })
  .catch(error => {
    console.error('Error:', error.response?.data || error.message);
  });`, 3)}
                  className="absolute top-4 right-4 p-2 text-slate-400 hover:text-white bg-slate-800 rounded transition-colors"
                  title="Copy to clipboard"
                >
                  {copiedIndex === 3 ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                </button>
                <pre className="text-cyan-300 font-mono text-sm overflow-x-auto">
{`const axios = require('axios');
const FormData = require('form-data');
const fs = require('fs');

const form = new FormData();
form.append('file', fs.createReadStream('exam.pdf'));

axios.post('http://localhost:8000/extract/json', form, {
  headers: form.getHeaders()
})
  .then(response => {
    const data = response.data;
    console.log(\`Processing time: \${data.processing_time}s\`);
    console.log(\`Pages processed: \${data.pages_processed}\`);
    
    // Save to file
    fs.writeFileSync('results.json', JSON.stringify(data, null, 2));
    console.log('Results saved to results.json');
  })
  .catch(error => {
    console.error('Error:', error.response?.data || error.message);
  });`}
                </pre>
              </div>
            </div>

            {/* Response Format */}
            <div className="mb-10">
              <h2 className="text-2xl font-bold text-slate-800 mb-4">Response Format</h2>
              <p className="text-slate-600 mb-4">
                The API returns a JSON object with the following structure:
              </p>
              <div className="bg-slate-50 border border-slate-200 rounded-lg p-4 relative">
                <button 
                  onClick={() => copyCode(`{
  "filename": "exam.pdf",
  "extraction_timestamp": "2025-12-04T23:15:30Z",
  "total_multiple_choice": 25,
  "total_free_response": 3,
  "pages_processed": 5,
  "processing_time": 8.42,
  "multiple_choice": [
    {
      "question": 1,
      "answer": "B",
      "page": 1
    },
    {
      "question": 2,
      "answer": "A",
      "page": 1
    }
  ],
  "free_response": [
    {
      "question": 1,
      "response": "The mitochondria is the powerhouse of the cell because it produces ATP through cellular respiration.",
      "page": 3
    },
    {
      "question": 2,
      "response": "Photosynthesis converts light energy into chemical energy stored in glucose.",
      "page": 4
    }
  ],
  "candidate_info": [
    {
      "name": "John Smith",
      "id": "12345",
      "country": "USA",
      "level": "Advanced",
      "page": 1
    }
  ]
}`, 4)}
                  className="absolute top-4 right-4 p-2 text-slate-600 hover:text-slate-800 bg-slate-200 rounded transition-colors"
                  title="Copy to clipboard"
                >
                  {copiedIndex === 4 ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                </button>
                <pre className="text-slate-700 font-mono text-sm overflow-x-auto">
{`{
  "filename": "exam.pdf",
  "extraction_timestamp": "2025-12-04T23:15:30Z",
  "total_multiple_choice": 25,
  "total_free_response": 3,
  "pages_processed": 5,
  "processing_time": 8.42,
  "multiple_choice": [
    {
      "question": 1,
      "answer": "B",
      "page": 1
    },
    {
      "question": 2,
      "answer": "A",
      "page": 1
    }
  ],
  "free_response": [
    {
      "question": 1,
      "response": "The mitochondria is the powerhouse...",
      "page": 3
    },
    {
      "question": 2,
      "response": "Photosynthesis converts light...",
      "page": 4
    }
  ],
  "candidate_info": [
    {
      "name": "John Smith",
      "id": "12345",
      "country": "USA",
      "level": "Advanced",
      "page": 1
    }
  ]
}`}
                </pre>
              </div>
            </div>

            {/* Field Descriptions */}
            <div className="mb-10">
              <h2 className="text-2xl font-bold text-slate-800 mb-4">Response Fields</h2>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-slate-100">
                    <tr>
                      <th className="text-left p-3 font-semibold">Field</th>
                      <th className="text-left p-3 font-semibold">Type</th>
                      <th className="text-left p-3 font-semibold">Description</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200">
                    <tr>
                      <td className="p-3 font-mono text-xs">filename</td>
                      <td className="p-3">string</td>
                      <td className="p-3">Original PDF filename</td>
                    </tr>
                    <tr>
                      <td className="p-3 font-mono text-xs">extraction_timestamp</td>
                      <td className="p-3">string</td>
                      <td className="p-3">ISO 8601 timestamp of extraction</td>
                    </tr>
                    <tr>
                      <td className="p-3 font-mono text-xs">total_multiple_choice</td>
                      <td className="p-3">integer</td>
                      <td className="p-3">Count of MCQ answers extracted</td>
                    </tr>
                    <tr>
                      <td className="p-3 font-mono text-xs">total_free_response</td>
                      <td className="p-3">integer</td>
                      <td className="p-3">Count of free response answers</td>
                    </tr>
                    <tr>
                      <td className="p-3 font-mono text-xs">pages_processed</td>
                      <td className="p-3">integer</td>
                      <td className="p-3">Number of pages in the PDF</td>
                    </tr>
                    <tr>
                      <td className="p-3 font-mono text-xs">processing_time</td>
                      <td className="p-3">float</td>
                      <td className="p-3">Total extraction time in seconds</td>
                    </tr>
                    <tr>
                      <td className="p-3 font-mono text-xs">multiple_choice</td>
                      <td className="p-3">array</td>
                      <td className="p-3">Array of MCQ answer objects</td>
                    </tr>
                    <tr>
                      <td className="p-3 font-mono text-xs">free_response</td>
                      <td className="p-3">array</td>
                      <td className="p-3">Array of free response objects</td>
                    </tr>
                    <tr>
                      <td className="p-3 font-mono text-xs">candidate_info</td>
                      <td className="p-3">array</td>
                      <td className="p-3">Candidate details from answer sheet</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            {/* Error Handling */}
            <div className="mb-10">
              <h2 className="text-2xl font-bold text-slate-800 mb-4">Error Handling</h2>
              <div className="space-y-4">
                <div className="border-l-4 border-red-500 bg-red-50 p-4">
                  <h3 className="font-bold text-red-900">400 Bad Request</h3>
                  <p className="text-red-700 text-sm">Invalid file format. Only PDF files are accepted.</p>
                  <pre className="text-xs mt-2 bg-red-100 p-2 rounded">{'{ "detail": "Only PDF files are allowed" }'}</pre>
                </div>
                <div className="border-l-4 border-yellow-500 bg-yellow-50 p-4">
                  <h3 className="font-bold text-yellow-900">500 Internal Server Error</h3>
                  <p className="text-yellow-700 text-sm">Extraction failed due to processing error.</p>
                  <pre className="text-xs mt-2 bg-yellow-100 p-2 rounded">{'{ "detail": "Extraction failed: <error message>" }'}</pre>
                </div>
              </div>
            </div>

            {/* Rate Limits */}
            <div className="mb-10">
              <h2 className="text-2xl font-bold text-slate-800 mb-4">Performance & Limits</h2>
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-6">
                <ul className="space-y-2 text-slate-700">
                  <li className="flex items-start gap-2">
                    <span className="text-blue-600 font-bold">•</span>
                    <span><strong>Max File Size:</strong> 50 MB per PDF</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-blue-600 font-bold">•</span>
                    <span><strong>Processing Speed:</strong> ~2-3 seconds per page (with parallel processing)</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-blue-600 font-bold">•</span>
                    <span><strong>Parallel Workers:</strong> 4 concurrent page processors (configurable)</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-blue-600 font-bold">•</span>
                    <span><strong>Supported Formats:</strong> PDF only</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-blue-600 font-bold">•</span>
                    <span><strong>Response Time:</strong> Synchronous (waits for completion before returning)</span>
                  </li>
                </ul>
              </div>
            </div>

            {/* Best Practices */}
            <div className="mb-10">
              <h2 className="text-2xl font-bold text-slate-800 mb-4">Best Practices</h2>
              <div className="space-y-3">
                <div className="bg-green-50 border-l-4 border-green-500 p-4">
                  <h3 className="font-semibold text-green-900">✓ Do's</h3>
                  <ul className="mt-2 space-y-1 text-green-800 text-sm">
                    <li>• Use high-quality scanned PDFs for best results</li>
                    <li>• Implement retry logic for network failures</li>
                    <li>• Validate extracted data in your application</li>
                    <li>• Monitor processing_time for performance tracking</li>
                  </ul>
                </div>
                <div className="bg-red-50 border-l-4 border-red-500 p-4">
                  <h3 className="font-semibold text-red-900">✗ Don'ts</h3>
                  <ul className="mt-2 space-y-1 text-red-800 text-sm">
                    <li>• Don't send low-resolution or blurry scans</li>
                    <li>• Don't upload non-exam PDFs (results will be inaccurate)</li>
                    <li>• Don't expect instant responses for large multi-page documents</li>
                    <li>• Don't ignore error responses</li>
                  </ul>
                </div>
              </div>
            </div>

            {/* Support */}
            <div className="bg-gradient-to-r from-blue-50 to-purple-50 border border-blue-200 rounded-lg p-6">
              <h2 className="text-xl font-bold text-slate-800 mb-3">Need Help?</h2>
              <p className="text-slate-600 mb-4">
                For technical support, feature requests, or bug reports, please check our documentation or contact the development team.
              </p>
              <div className="flex gap-3">
                <Link to="/" className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
                  Try Upload Demo
                </Link>
                <a href="http://localhost:8000/docs" target="_blank" rel="noopener noreferrer" className="px-4 py-2 bg-slate-200 text-slate-700 rounded-lg hover:bg-slate-300 transition-colors">
                  OpenAPI Docs
                </a>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ApiDocsPage;
