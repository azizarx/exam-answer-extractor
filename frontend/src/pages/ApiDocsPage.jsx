import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, Code, Copy, Check, Zap, Key, CheckCircle, Database, FileJson, BookOpen } from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

const ApiDocsPage = () => {
  const [copiedIndex, setCopiedIndex] = useState(null);
  const [activeTab, setActiveTab] = useState('extract');

  const copyCode = (code, index) => {
    navigator.clipboard.writeText(code);
    setCopiedIndex(index);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  const CopyBtn = ({ code, idx }) => (
    <button
      onClick={() => copyCode(code, idx)}
      className="absolute top-3 right-3 p-2 text-slate-400 hover:text-white bg-slate-700 rounded transition-colors"
      title="Copy to clipboard"
    >
      {copiedIndex === idx ? <Check className="w-4 h-4 text-green-400" /> : <Copy className="w-4 h-4" />}
    </button>
  );

  const Endpoint = ({ method, path, desc, children }) => {
    const colors = {
      GET: 'bg-green-100 text-green-800 border-green-300',
      POST: 'bg-blue-100 text-blue-800 border-blue-300',
      DELETE: 'bg-red-100 text-red-800 border-red-300',
    };
    return (
      <div className="border border-slate-200 rounded-lg mb-4 overflow-hidden">
        <div className="flex items-center gap-3 p-4 bg-slate-50 border-b border-slate-200">
          <span className={`px-2 py-1 text-xs font-bold rounded border ${colors[method]}`}>{method}</span>
          <code className="text-sm font-semibold text-slate-800">{path}</code>
          <span className="text-slate-500 text-sm ml-auto hidden sm:inline">{desc}</span>
        </div>
        {children && <div className="p-4">{children}</div>}
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50">
      {/* Hero */}
      <div className="bg-gradient-to-r from-blue-700 to-indigo-800 text-white">
        <div className="max-w-6xl mx-auto px-4 py-12">
          <Link to="/" className="inline-flex items-center gap-2 text-blue-200 hover:text-white mb-6 transition-colors">
            <ArrowLeft className="w-4 h-4" /> Back to Upload
          </Link>
          <div className="flex items-center gap-3 mb-4">
            <Code className="w-10 h-10" />
            <h1 className="text-4xl font-bold">Exam Extractor API</h1>
          </div>
          <p className="text-blue-100 text-lg max-w-2xl">
            Server-to-server PDF → JSON extraction. Primary endpoint: <code className="bg-blue-600/50 px-1 rounded">POST /extract/json</code>.
            This UI is for testing; integrators should use OpenAPI or <code className="bg-blue-600/50 px-1 rounded">docs/API.md</code>.
          </p>
          <div className="flex gap-3 mt-6 flex-wrap">
            <span className="bg-blue-600/40 px-3 py-1 rounded-full text-sm">Base URL: {API_BASE}</span>
            <span className="bg-blue-600/40 px-3 py-1 rounded-full text-sm">Content: multipart/form-data</span>
            <span className="bg-blue-600/40 px-3 py-1 rounded-full text-sm">Auth: optional X-API-Key</span>
          </div>
        </div>
      </div>

      {/* Feature Grid */}
      <div className="max-w-6xl mx-auto px-4 -mt-6">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          {[
            { icon: <Zap className="w-6 h-6 text-yellow-600" />, title: 'Sync Extraction', text: 'Upload PDF, get JSON instantly' },
            { icon: <CheckCircle className="w-6 h-6 text-green-600" />, title: 'Auto-Marking', text: 'Compare answers to key automatically' },
            { icon: <Key className="w-6 h-6 text-purple-600" />, title: 'Answer Key CRUD', text: 'Store & manage answer keys' },
            { icon: <Database className="w-6 h-6 text-blue-600" />, title: 'Submission Tracking', text: 'Background processing + status' },
          ].map((f, i) => (
            <div key={i} className="bg-white rounded-xl shadow-md p-5 flex items-start gap-3">
              {f.icon}
              <div>
                <h3 className="font-semibold text-slate-800">{f.title}</h3>
                <p className="text-sm text-slate-500">{f.text}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Tabs */}
      <div className="max-w-6xl mx-auto px-4 mt-10">
        <div className="flex flex-wrap gap-2 border-b border-slate-200 pb-1">
          {[
            { id: 'extract', label: 'Extraction', icon: <FileJson className="w-4 h-4" /> },
            { id: 'marking', label: 'Marking', icon: <CheckCircle className="w-4 h-4" /> },
            { id: 'answerkeys', label: 'Answer Keys', icon: <Key className="w-4 h-4" /> },
            { id: 'submissions', label: 'Submissions', icon: <Database className="w-4 h-4" /> },
            { id: 'examples', label: 'Code Examples', icon: <Code className="w-4 h-4" /> },
            { id: 'reference', label: 'Reference', icon: <BookOpen className="w-4 h-4" /> },
          ].map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${activeTab === tab.id ? 'bg-white text-blue-700 border border-b-0 border-slate-200' : 'text-slate-500 hover:text-slate-700'}`}
            >
              {tab.icon} {tab.label}
            </button>
          ))}
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-4 py-8">

        {/* EXTRACTION TAB */}
        {activeTab === 'extract' && (
          <div>
            <h2 className="text-2xl font-bold text-slate-800 mb-2">Extraction Endpoints</h2>
            <p className="text-slate-600 mb-6">Upload a scanned exam PDF and receive structured JSON with per-candidate answers.</p>

            <Endpoint method="POST" path="/extract/json" desc="Synchronous extraction — returns JSON immediately">
              <p className="text-sm text-slate-600 mb-3">
                Upload a PDF and receive structured candidate data in the response. No submission DB row.
                This is the primary endpoint for third-party servers. Omit <code className="bg-slate-100 px-1 rounded">template_id</code> to auto-detect layout per page from the footer.
                Use a client timeout of at least 10 minutes for multi-page PDFs.
              </p>
              <h4 className="font-semibold text-slate-700 mb-2">Request (auto layout)</h4>
              <div className="bg-slate-800 rounded-lg p-4 relative mb-4">
                <CopyBtn code={`curl -X POST ${API_BASE}/extract/json \\\n  -H "X-API-Key: $API_KEY" \\\n  -F "file=@exam_sheet.pdf"`} idx="e1" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">{`curl -X POST ${API_BASE}/extract/json \\
  -H "X-API-Key: $API_KEY" \\
  -F "file=@exam_sheet.pdf"`}</pre>
              </div>
              <h4 className="font-semibold text-slate-700 mb-2">Request (forced template)</h4>
              <div className="bg-slate-800 rounded-lg p-4 relative mb-4">
                <CopyBtn code={`curl -X POST "${API_BASE}/extract/json?template_id=seamo_2025_b" \\\n  -H "X-API-Key: $API_KEY" \\\n  -F "file=@exam_sheet.pdf"`} idx="e1b" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">{`curl -X POST "${API_BASE}/extract/json?template_id=seamo_2025_b" \\
  -H "X-API-Key: $API_KEY" \\
  -F "file=@exam_sheet.pdf"`}</pre>
              </div>
              <p className="text-xs text-slate-500 mb-4">
                List ids via <code className="bg-slate-100 px-1 rounded">GET /templates/all</code>. If the server has <code className="bg-slate-100 px-1 rounded">API_KEY</code> unset, omit the header.
              </p>

              <h4 className="font-semibold text-slate-700 mb-2">Response (200 OK)</h4>
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code={'{\n  "document_information": {\n    "filename": "exam_sheet.pdf",\n    "extraction_timestamp": "2025-06-03T12:30:00Z",\n    "pages_processed": 35,\n    "pages_with_data": 35,\n    "processing_time": 45.2,\n    "total_candidates": 35\n  },\n  "candidates": [\n    {\n      "candidate_name": "JOHN SMITH",\n      "candidate_number": "12345",\n      "country": "USA",\n      "paper_type": "PAPER A",\n      "answers": {\n        "1": "D", "2": "B", "3": "A",\n        "4": "BL", "5": "C", "6": "IN"\n      },\n      "drawing_questions": {\n        "31": "Student drew a circle with radius 5cm"\n      }\n    }\n  ],\n  "validation": { "is_valid": true, "warnings": [] }\n}'} idx="e2" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">{`{
  "document_information": {
    "filename": "exam_sheet.pdf",
    "extraction_timestamp": "2025-06-03T12:30:00Z",
    "pages_processed": 35,
    "pages_with_data": 35,
    "processing_time": 45.2,
    "total_candidates": 35
  },
  "candidates": [
    {
      "candidate_name": "JOHN SMITH",
      "candidate_number": "12345",
      "country": "USA",
      "paper_type": "PAPER A",
      "answers": {
        "1": "D",  "2": "B",  "3": "A",
        "4": "BL", "5": "C",  "6": "IN"
      },
      "drawing_questions": {
        "31": "Student drew a circle with radius 5cm"
      }
    },
    ...
  ],
  "validation": { "is_valid": true, "warnings": [] }
}`}</pre>
              </div>

              <div className="mt-4 grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="bg-yellow-50 border border-yellow-200 rounded p-3">
                  <span className="font-mono text-xs font-bold text-yellow-700">BL</span>
                  <p className="text-xs text-yellow-800 mt-1">Blank — candidate left the question unanswered</p>
                </div>
                <div className="bg-orange-50 border border-orange-200 rounded p-3">
                  <span className="font-mono text-xs font-bold text-orange-700">IN</span>
                  <p className="text-xs text-orange-800 mt-1">Invalid — multiple answers selected or unreadable</p>
                </div>
                <div className="bg-blue-50 border border-blue-200 rounded p-3">
                  <span className="font-mono text-xs font-bold text-blue-700">A, B, C, D</span>
                  <p className="text-xs text-blue-800 mt-1">The candidate&#39;s selected answer letter</p>
                </div>
              </div>
            </Endpoint>
          </div>
        )}

        {/* MARKING TAB */}
        {activeTab === 'marking' && (
          <div>
            <h2 className="text-2xl font-bold text-slate-800 mb-2">Marking Endpoints</h2>
            <p className="text-slate-600 mb-6">Auto-mark candidate answers against an answer key. Supports inline keys or stored answer keys.</p>

            <Endpoint method="POST" path="/extract/json/mark" desc="Extract PDF + mark against a stored answer key">
              <p className="text-sm text-slate-600 mb-3">
                Extract then mark against a <strong>stored</strong> answer key. Inline keys are rejected.
                Optional <code className="bg-slate-100 px-1 rounded">template_id</code> forces layout; omit for auto-detect.
              </p>

              <h4 className="font-semibold text-slate-700 mb-2">With stored answer key ID</h4>
              <div className="bg-slate-800 rounded-lg p-4 relative mb-4">
                <CopyBtn code={`curl -X POST "${API_BASE}/extract/json/mark?template_id=seamo_2025_a" \\\n  -H "X-API-Key: $API_KEY" \\\n  -F "file=@exam_sheet.pdf" \\\n  -F 'mark_request={"answer_key_id": 1}'`} idx="m2" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">{`curl -X POST "${API_BASE}/extract/json/mark?template_id=seamo_2025_a" \\
  -H "X-API-Key: $API_KEY" \\
  -F "file=@exam_sheet.pdf" \\
  -F 'mark_request={"answer_key_id": 1}'`}</pre>
              </div>

              <h4 className="font-semibold text-slate-700 mb-2">Auto-match (no mark_request)</h4>
              <p className="text-xs text-slate-500 mb-4">
                Uses the active stored key for the forced <code className="bg-slate-100 px-1 rounded">template_id</code>,
                or (auto mode) the active key matching each candidate&apos;s <code className="bg-slate-100 px-1 rounded">template_id</code>.
                Candidates without a matching key are returned unmarked.
              </p>

              <h4 className="font-semibold text-slate-700 mb-2">Marked Response</h4>
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code={'{\n  "filename": "exam_sheet.pdf",\n  "template_id": "seamo_2025_a",\n  "mode": "forced",\n  "total_candidates": 1,\n  "candidates": [\n    {\n      "candidate_name": "JOHN SMITH",\n      "answers": { "1": "D", "2": "A" },\n      "marking": {\n        "awarded_marks": 3,\n        "max_marks": 100,\n        "percentage": 3.0,\n        "answer_key_id": 1,\n        "outcomes": []\n      }\n    }\n  ]\n}'} idx="m3" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">{`{
  "filename": "exam_sheet.pdf",
  "template_id": "seamo_2025_a",
  "mode": "forced",
  "total_candidates": 1,
  "candidates": [
    {
      "candidate_name": "JOHN SMITH",
      "answers": { "1": "D", "2": "A" },
      "marking": {
        "awarded_marks": 3,
        "max_marks": 100,
        "percentage": 3.0,
        "answer_key_id": 1,
        "outcomes": []
      }
    }
  ]
}`}</pre>
              </div>

              <h4 className="font-semibold text-slate-700 mt-4 mb-2">Marking Codes</h4>
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                <div className="bg-green-50 border border-green-200 rounded p-2 text-center">
                  <span className="font-mono text-sm font-bold text-green-700">P</span>
                  <p className="text-xs text-green-600 mt-1">Correct</p>
                </div>
                <div className="bg-yellow-50 border border-yellow-200 rounded p-2 text-center">
                  <span className="font-mono text-sm font-bold text-yellow-700">BL</span>
                  <p className="text-xs text-yellow-600 mt-1">Blank</p>
                </div>
                <div className="bg-orange-50 border border-orange-200 rounded p-2 text-center">
                  <span className="font-mono text-sm font-bold text-orange-700">IN</span>
                  <p className="text-xs text-orange-600 mt-1">Invalid</p>
                </div>
                <div className="bg-red-50 border border-red-200 rounded p-2 text-center">
                  <span className="font-mono text-sm font-bold text-red-700">IM</span>
                  <p className="text-xs text-red-600 mt-1">Incorrect Drawing</p>
                </div>
                <div className="bg-slate-100 border border-slate-200 rounded p-2 text-center">
                  <span className="font-mono text-sm font-bold text-slate-700">A/B/C/D</span>
                  <p className="text-xs text-slate-600 mt-1">Wrong letter</p>
                </div>
              </div>
            </Endpoint>

            <Endpoint method="POST" path="/submission/{'{submission_id}'}/mark" desc="Mark a previously uploaded submission">
              <p className="text-sm text-slate-600 mb-3">
                Mark all candidate results stored from a background upload. Send a JSON body with either <code className="bg-slate-100 px-1 rounded">answer_key_id</code> or an inline <code className="bg-slate-100 px-1 rounded">answer_key</code> dict.
              </p>
              <div className="bg-slate-800 rounded-lg p-4 relative mb-3">
                <CopyBtn code={'# Using stored answer key:\ncurl -X POST http://localhost:8000/submission/1/mark \\\n  -H "Content-Type: application/json" \\\n  -d \'{"answer_key_id": 1}\'\n\n# Using inline answer key:\ncurl -X POST http://localhost:8000/submission/1/mark \\\n  -H "Content-Type: application/json" \\\n  -d \'{"answer_key":{"1":"D","2":"B","3":"A"},"drawing_key":{"31":"circle"}}\''} idx="m4" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">{`# Using stored answer key:
curl -X POST http://localhost:8000/submission/1/mark \\
  -H "Content-Type: application/json" \\
  -d '{"answer_key_id": 1}'

# Using inline answer key:
curl -X POST http://localhost:8000/submission/1/mark \\
  -H "Content-Type: application/json" \\
  -d '{"answer_key":{"1":"D","2":"B","3":"A"},"drawing_key":{"31":"circle"}}'`}</pre>
              </div>

              <h4 className="font-semibold text-slate-700 mb-2">Response</h4>
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code={'{\n  "status": "success",\n  "submission_id": 1,\n  "total_candidates_marked": 35,\n  "results": [\n    {\n      "candidate_name": "JOHN SMITH",\n      "candidate_number": "12345",\n      "marked_answers": { "1": "P", "2": "B", "3": "P" },\n      "marked_drawing": { "31": "P" },\n      "score": { "correct": 2, "total": 3, "percentage": 66.7 }\n    }\n  ]\n}'} idx="m5" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">{`{
  "status": "success",
  "submission_id": 1,
  "total_candidates_marked": 35,
  "results": [
    {
      "candidate_name": "JOHN SMITH",
      "candidate_number": "12345",
      "marked_answers": { "1": "P", "2": "B", "3": "P" },
      "marked_drawing": { "31": "P" },
      "score": { "correct": 2, "total": 3, "percentage": 66.7 }
    }
  ]
}`}</pre>
              </div>
              <div className="mt-3 p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
                <strong>Note:</strong> Marking results are persisted in the database. Retrieve them later via <code className="bg-amber-100 px-1 rounded">GET /submission/{'{id}'}</code>.
              </div>
            </Endpoint>
          </div>
        )}

        {/* ANSWER KEYS TAB */}
        {activeTab === 'answerkeys' && (
          <div>
            <h2 className="text-2xl font-bold text-slate-800 mb-2">Answer Key Management</h2>
            <p className="text-slate-600 mb-6">Create, list, retrieve, and delete answer keys used for auto-marking.</p>

            <Endpoint method="POST" path="/answer-keys" desc="Create a new answer key">
              <p className="text-sm text-slate-600 mb-3">
                Store an answer key for a specific paper type. The key can later be referenced by ID or auto-matched by paper type during marking.
              </p>
              <div className="bg-slate-800 rounded-lg p-4 relative mb-3">
                <CopyBtn code={'curl -X POST http://localhost:8000/answer-keys \\\n  -H "Content-Type: application/json" \\\n  -d \'{\n    "name": "2025 Paper A Answer Key",\n    "paper_type": "PAPER A",\n    "answers": {\n      "1": "D", "2": "B", "3": "A", "4": "C", "5": "B",\n      "6": "A", "7": "D", "8": "C", "9": "B", "10": "A"\n    },\n    "drawing_key": { "31": "circle", "32": "triangle" }\n  }\''} idx="a1" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">{`curl -X POST http://localhost:8000/answer-keys \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "2025 Paper A Answer Key",
    "paper_type": "PAPER A",
    "answers": {
      "1": "D", "2": "B", "3": "A", "4": "C", "5": "B",
      "6": "A", "7": "D", "8": "C", "9": "B", "10": "A"
    },
    "drawing_key": { "31": "circle", "32": "triangle" }
  }'`}</pre>
              </div>
              <h4 className="font-semibold text-slate-700 mb-2">Response (200)</h4>
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code={'{\n  "id": 1,\n  "name": "2025 Paper A Answer Key",\n  "paper_type": "PAPER A",\n  "answers": { "1": "D", "2": "B", ... },\n  "drawing_key": { "31": "circle", "32": "triangle" },\n  "total_questions": 10,\n  "created_at": "2025-06-03T12:00:00Z",\n  "updated_at": "2025-06-03T12:00:00Z"\n}'} idx="a2" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">{`{
  "id": 1,
  "name": "2025 Paper A Answer Key",
  "paper_type": "PAPER A",
  "answers": { "1": "D", "2": "B", ... },
  "drawing_key": { "31": "circle", "32": "triangle" },
  "total_questions": 10,
  "created_at": "2025-06-03T12:00:00Z",
  "updated_at": "2025-06-03T12:00:00Z"
}`}</pre>
              </div>
            </Endpoint>

            <Endpoint method="GET" path="/answer-keys" desc="List all stored answer keys">
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code="curl http://localhost:8000/answer-keys" idx="a3" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">curl http://localhost:8000/answer-keys</pre>
              </div>
              <p className="text-xs text-slate-500 mt-2">Returns an array of all answer key objects.</p>
            </Endpoint>

            <Endpoint method="GET" path="/answer-keys/{'{key_id}'}" desc="Get a specific answer key">
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code="curl http://localhost:8000/answer-keys/1" idx="a4" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">curl http://localhost:8000/answer-keys/1</pre>
              </div>
            </Endpoint>

            <Endpoint method="DELETE" path="/answer-keys/{'{key_id}'}" desc="Delete an answer key">
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code="curl -X DELETE http://localhost:8000/answer-keys/1" idx="a5" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">curl -X DELETE http://localhost:8000/answer-keys/1</pre>
              </div>
              <p className="text-xs text-slate-500 mt-2">Returns <code className="bg-slate-100 px-1 rounded">{`{"status": "deleted"}`}</code></p>
            </Endpoint>

            <div className="bg-purple-50 border border-purple-200 rounded-lg p-4 mt-4">
              <h4 className="font-semibold text-purple-800 mb-2">Answer Key Body Fields</h4>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-purple-700">
                      <th className="pb-2">Field</th>
                      <th className="pb-2">Type</th>
                      <th className="pb-2">Required</th>
                      <th className="pb-2">Description</th>
                    </tr>
                  </thead>
                  <tbody className="text-purple-900 divide-y divide-purple-100">
                    <tr><td className="py-1 font-mono text-xs">name</td><td>string</td><td>Yes</td><td>Name of the answer key</td></tr>
                    <tr><td className="py-1 font-mono text-xs">paper_type</td><td>string</td><td>No</td><td>Paper type for auto-matching (e.g. &quot;PAPER A&quot;)</td></tr>
                    <tr><td className="py-1 font-mono text-xs">answers</td><td>object</td><td>Yes</td><td>{`{"1": "D", "2": "B", ...}`} correct MCQ answers</td></tr>
                    <tr><td className="py-1 font-mono text-xs">drawing_key</td><td>object</td><td>No</td><td>{`{"31": "circle"}`} keywords for drawing questions</td></tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* SUBMISSIONS TAB */}
        {activeTab === 'submissions' && (
          <div>
            <h2 className="text-2xl font-bold text-slate-800 mb-2">Submission Management</h2>
            <p className="text-slate-600 mb-6">Upload PDFs for background processing, track status, retrieve results, and manage submissions.</p>

            <Endpoint method="POST" path="/upload" desc="Upload PDF for background processing">
              <p className="text-sm text-slate-600 mb-3">
                Upload a PDF to be processed in the background. Returns a <code className="bg-slate-100 px-1 rounded">submission_id</code> you can use to check status and retrieve results later.
              </p>
              <div className="bg-slate-800 rounded-lg p-4 relative mb-3">
                <CopyBtn code={'curl -X POST http://localhost:8000/upload -F "file=@exam.pdf"'} idx="s1" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">{'curl -X POST http://localhost:8000/upload -F "file=@exam.pdf"'}</pre>
              </div>
              <h4 className="font-semibold text-slate-700 mb-2">Response</h4>
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code={'{\n  "status": "success",\n  "message": "File uploaded and processing started",\n  "submission_id": 1,\n  "filename": "exam.pdf",\n  "storage_path": "uploads/20250603_abc123_exam.pdf"\n}'} idx="s2" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">{`{
  "status": "success",
  "message": "File uploaded and processing started",
  "submission_id": 1,
  "filename": "exam.pdf",
  "storage_path": "uploads/20250603_abc123_exam.pdf"
}`}</pre>
              </div>
            </Endpoint>

            <Endpoint method="GET" path="/status/{'{submission_id}'}" desc="Check processing status">
              <div className="bg-slate-800 rounded-lg p-4 relative mb-3">
                <CopyBtn code="curl http://localhost:8000/status/1" idx="s3" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">curl http://localhost:8000/status/1</pre>
              </div>
              <h4 className="font-semibold text-slate-700 mb-2">Response</h4>
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code={'{\n  "submission_id": 1,\n  "filename": "exam.pdf",\n  "status": "completed",\n  "created_at": "2025-06-03T12:00:00Z",\n  "processed_at": "2025-06-03T12:01:30Z",\n  "pages_count": 35,\n  "candidates_count": 35\n}'} idx="s4" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">{`{
  "submission_id": 1,
  "filename": "exam.pdf",
  "status": "completed",       // pending | processing | completed | failed
  "created_at": "2025-06-03T12:00:00Z",
  "processed_at": "2025-06-03T12:01:30Z",
  "pages_count": 35,
  "candidates_count": 35
}`}</pre>
              </div>
            </Endpoint>

            <Endpoint method="GET" path="/submission/{'{submission_id}'}" desc="Get candidate results">
              <p className="text-sm text-slate-600 mb-3">Returns all extracted candidate data for a completed submission.</p>
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code="curl http://localhost:8000/submission/1" idx="s5" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">{`curl http://localhost:8000/submission/1

# Response includes candidates array:
# { "submission_id": 1, "filename": "...", "status": "completed",
#   "candidates": [ { "candidate_name": "...", "answers": {...} } ] }`}</pre>
              </div>
            </Endpoint>

            <Endpoint method="GET" path="/submissions" desc="List all submissions">
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code="curl http://localhost:8000/submissions" idx="s6" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">curl http://localhost:8000/submissions</pre>
              </div>
              <p className="text-xs text-slate-500 mt-2">Returns an array of submission status objects.</p>
            </Endpoint>

            <Endpoint method="GET" path="/submission/{'{submission_id}'}/json" desc="Download raw JSON result file">
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code="curl http://localhost:8000/submission/1/json" idx="s7" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">curl http://localhost:8000/submission/1/json</pre>
              </div>
              <p className="text-xs text-slate-500 mt-2">Returns the full JSON file as stored on disk.</p>
            </Endpoint>

            <Endpoint method="GET" path="/submission/{'{submission_id}'}/logs" desc="View processing logs">
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code="curl http://localhost:8000/submission/1/logs" idx="s8" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">curl http://localhost:8000/submission/1/logs</pre>
              </div>
            </Endpoint>

            <Endpoint method="DELETE" path="/submission/{'{submission_id}'}" desc="Delete a submission">
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code="curl -X DELETE http://localhost:8000/submission/1" idx="s9" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">curl -X DELETE http://localhost:8000/submission/1</pre>
              </div>
              <p className="text-xs text-slate-500 mt-2">Deletes the submission, candidate results, logs, and stored files.</p>
            </Endpoint>
          </div>
        )}

        {/* CODE EXAMPLES TAB */}
        {activeTab === 'examples' && (
          <div>
            <h2 className="text-2xl font-bold text-slate-800 mb-2">Code Examples</h2>
            <p className="text-slate-600 mb-6">Copy-paste examples for Python, JavaScript, and Node.js.</p>

            <div className="mb-8">
              <h3 className="text-lg font-bold text-slate-800 mb-3">&#x1F40D; Python &mdash; Extract &amp; Mark</h3>
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code={`import requests, json

BASE = "http://localhost:8000"

# 1. Simple extraction
with open("exam_sheet.pdf", "rb") as f:
    resp = requests.post(f"{BASE}/extract/json", files={"file": f})
data = resp.json()

print(f"Candidates found: {len(data['candidates'])}")
for c in data["candidates"]:
    print(f"  {c['candidate_name']} ({c['candidate_number']}) - {c['paper_type']}")
    print(f"    Answers: {c['answers']}")

# 2. Extract + mark with inline answer key
answer_key = {"1": "D", "2": "B", "3": "A", "4": "C", "5": "B"}
drawing_key = {"31": "circle"}

with open("exam_sheet.pdf", "rb") as f:
    resp = requests.post(
        f"{BASE}/extract/json/mark",
        files={"file": f},
        data={"mark_request": json.dumps({
            "answer_key": answer_key,
            "drawing_key": drawing_key,
        })}
    )
marked = resp.json()

for c in marked["candidates"]:
    score = c.get("score", {})
    print(f"{c['candidate_name']}: {score.get('correct')}/{score.get('total')} "
          f"({score.get('percentage')}%)")

# 3. Store an answer key for reuse
ak_resp = requests.post(f"{BASE}/answer-keys", json={
    "name": "2025 Paper A",
    "paper_type": "PAPER A",
    "answers": answer_key,
    "drawing_key": drawing_key,
})
key_id = ak_resp.json()["id"]
print(f"Answer key created with ID: {key_id}")

# 4. Mark using stored answer key ID
with open("exam_sheet.pdf", "rb") as f:
    resp = requests.post(
        f"{BASE}/extract/json/mark",
        files={"file": f},
        data={"mark_request": json.dumps({"answer_key_id": key_id})}
    )`} idx="py1" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">{`import requests, json

BASE = "http://localhost:8000"

# 1. Simple extraction
with open("exam_sheet.pdf", "rb") as f:
    resp = requests.post(f"{BASE}/extract/json", files={"file": f})
data = resp.json()

print(f"Candidates found: {len(data['candidates'])}")
for c in data["candidates"]:
    print(f"  {c['candidate_name']} ({c['candidate_number']}) - {c['paper_type']}")
    print(f"    Answers: {c['answers']}")

# 2. Extract + mark with inline answer key
answer_key = {"1": "D", "2": "B", "3": "A", "4": "C", "5": "B"}
drawing_key = {"31": "circle"}

with open("exam_sheet.pdf", "rb") as f:
    resp = requests.post(
        f"{BASE}/extract/json/mark",
        files={"file": f},
        data={"mark_request": json.dumps({
            "answer_key": answer_key,
            "drawing_key": drawing_key,
        })}
    )
marked = resp.json()

for c in marked["candidates"]:
    score = c.get("score", {})
    print(f"{c['candidate_name']}: {score.get('correct')}/{score.get('total')} "
          f"({score.get('percentage')}%)")

# 3. Store an answer key for reuse
ak_resp = requests.post(f"{BASE}/answer-keys", json={
    "name": "2025 Paper A",
    "paper_type": "PAPER A",
    "answers": answer_key,
    "drawing_key": drawing_key,
})
key_id = ak_resp.json()["id"]
print(f"Answer key created with ID: {key_id}")

# 4. Mark using stored answer key ID
with open("exam_sheet.pdf", "rb") as f:
    resp = requests.post(
        f"{BASE}/extract/json/mark",
        files={"file": f},
        data={"mark_request": json.dumps({"answer_key_id": key_id})}
    )`}</pre>
              </div>
            </div>

            <div className="mb-8">
              <h3 className="text-lg font-bold text-slate-800 mb-3">&#x1F310; JavaScript (Browser) &mdash; Fetch API</h3>
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code={`const BASE = "http://localhost:8000";

// 1. Extract answers from PDF
async function extractPDF(file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(BASE + "/extract/json", { method: "POST", body: form });
  const data = await res.json();
  console.log("Found " + data.candidates.length + " candidates");
  data.candidates.forEach(c => console.log(c.candidate_name + ": ", c.answers));
  return data;
}

// 2. Extract + mark with inline key
async function extractAndMark(file, answerKey, drawingKey) {
  const form = new FormData();
  form.append("file", file);
  form.append("mark_request", JSON.stringify({
    answer_key: answerKey, drawing_key: drawingKey
  }));
  const res = await fetch(BASE + "/extract/json/mark", { method: "POST", body: form });
  const data = await res.json();
  data.candidates.forEach(c => {
    const s = c.score || {};
    console.log(c.candidate_name + ": " + s.correct + "/" + s.total + " (" + s.percentage + "%)");
  });
  return data;
}

// 3. Create answer key
async function createAnswerKey(name, paperType, answers, drawingKey) {
  const res = await fetch(BASE + "/answer-keys", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, paper_type: paperType, answers, drawing_key: drawingKey }),
  });
  return res.json();
}

// Usage with file input
document.querySelector('input[type="file"]')
  .addEventListener("change", e => extractPDF(e.target.files[0]));`} idx="js1" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">{`const BASE = "http://localhost:8000";

// 1. Extract answers from PDF
async function extractPDF(file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(BASE + "/extract/json", { method: "POST", body: form });
  const data = await res.json();
  console.log("Found " + data.candidates.length + " candidates");
  data.candidates.forEach(c => console.log(c.candidate_name + ": ", c.answers));
  return data;
}

// 2. Extract + mark with inline key
async function extractAndMark(file, answerKey, drawingKey) {
  const form = new FormData();
  form.append("file", file);
  form.append("mark_request", JSON.stringify({
    answer_key: answerKey, drawing_key: drawingKey
  }));
  const res = await fetch(BASE + "/extract/json/mark", { method: "POST", body: form });
  const data = await res.json();
  data.candidates.forEach(c => {
    const s = c.score || {};
    console.log(c.candidate_name + ": " + s.correct + "/" + s.total);
  });
  return data;
}

// 3. Create answer key
async function createAnswerKey(name, paperType, answers, drawingKey) {
  const res = await fetch(BASE + "/answer-keys", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, paper_type: paperType, answers, drawing_key: drawingKey }),
  });
  return res.json();
}

// Usage with file input
document.querySelector('input[type="file"]')
  .addEventListener("change", e => extractPDF(e.target.files[0]));`}</pre>
              </div>
            </div>

            <div className="mb-8">
              <h3 className="text-lg font-bold text-slate-800 mb-3">&#x1F7E2; Node.js &mdash; axios + FormData</h3>
              <div className="bg-slate-800 rounded-lg p-4 relative">
                <CopyBtn code={`const axios = require("axios");
const FormData = require("form-data");
const fs = require("fs");
const BASE = "http://localhost:8000";

async function main() {
  // 1. Extract
  const form = new FormData();
  form.append("file", fs.createReadStream("exam_sheet.pdf"));
  const { data } = await axios.post(BASE + "/extract/json", form, {
    headers: form.getHeaders(),
  });
  console.log("Candidates: " + data.candidates.length);
  data.candidates.forEach(c => {
    console.log("  " + c.candidate_name + " (" + c.candidate_number + ")");
    console.log("    Answers:", JSON.stringify(c.answers));
  });

  // 2. Extract + mark
  const markForm = new FormData();
  markForm.append("file", fs.createReadStream("exam_sheet.pdf"));
  markForm.append("mark_request", JSON.stringify({
    answer_key: { "1": "D", "2": "B", "3": "A", "4": "C" },
    drawing_key: { "31": "circle" },
  }));
  const { data: marked } = await axios.post(
    BASE + "/extract/json/mark", markForm, { headers: markForm.getHeaders() }
  );
  marked.candidates.forEach(c => {
    const s = c.score || {};
    console.log("  " + c.candidate_name + ": " + s.correct + "/" + s.total);
  });

  // 3. Manage answer keys
  const { data: ak } = await axios.post(BASE + "/answer-keys", {
    name: "2025 Paper A", paper_type: "PAPER A",
    answers: { "1": "D", "2": "B", "3": "A" },
  });
  console.log("Created answer key ID: " + ak.id);

  const { data: allKeys } = await axios.get(BASE + "/answer-keys");
  console.log("Total answer keys: " + allKeys.length);
}
main().catch(console.error);`} idx="nd1" />
                <pre className="text-green-300 font-mono text-sm overflow-x-auto">{`const axios = require("axios");
const FormData = require("form-data");
const fs = require("fs");
const BASE = "http://localhost:8000";

async function main() {
  // 1. Extract
  const form = new FormData();
  form.append("file", fs.createReadStream("exam_sheet.pdf"));
  const { data } = await axios.post(BASE + "/extract/json", form, {
    headers: form.getHeaders(),
  });
  console.log("Candidates: " + data.candidates.length);
  data.candidates.forEach(c => {
    console.log("  " + c.candidate_name + " (" + c.candidate_number + ")");
    console.log("    Answers:", JSON.stringify(c.answers));
  });

  // 2. Extract + mark
  const markForm = new FormData();
  markForm.append("file", fs.createReadStream("exam_sheet.pdf"));
  markForm.append("mark_request", JSON.stringify({
    answer_key: { "1": "D", "2": "B", "3": "A", "4": "C" },
    drawing_key: { "31": "circle" },
  }));
  const { data: marked } = await axios.post(
    BASE + "/extract/json/mark", markForm, { headers: markForm.getHeaders() }
  );
  marked.candidates.forEach(c => {
    const s = c.score || {};
    console.log("  " + c.candidate_name + ": " + s.correct + "/" + s.total);
  });

  // 3. Manage answer keys
  const { data: ak } = await axios.post(BASE + "/answer-keys", {
    name: "2025 Paper A", paper_type: "PAPER A",
    answers: { "1": "D", "2": "B", "3": "A" },
  });
  console.log("Created answer key ID: " + ak.id);

  const { data: allKeys } = await axios.get(BASE + "/answer-keys");
  console.log("Total answer keys: " + allKeys.length);
}
main().catch(console.error);`}</pre>
              </div>
            </div>
          </div>
        )}

        {/* REFERENCE TAB */}
        {activeTab === 'reference' && (
          <div>
            <h2 className="text-2xl font-bold text-slate-800 mb-6">API Reference</h2>

            <div className="mb-8">
              <h3 className="text-lg font-bold text-slate-800 mb-3">All Endpoints</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm border border-slate-200 rounded-lg overflow-hidden">
                  <thead className="bg-slate-100">
                    <tr>
                      <th className="text-left p-3 font-semibold">Method</th>
                      <th className="text-left p-3 font-semibold">Path</th>
                      <th className="text-left p-3 font-semibold">Description</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200">
                    {[
                      ['POST', '/extract/json', 'Extract PDF to JSON (synchronous)'],
                      ['POST', '/extract/json/mark', 'Extract + auto-mark (synchronous)'],
                      ['POST', '/answer-keys', 'Create an answer key'],
                      ['GET', '/answer-keys', 'List all answer keys'],
                      ['GET', '/answer-keys/{key_id}', 'Get specific answer key'],
                      ['DELETE', '/answer-keys/{key_id}', 'Delete an answer key'],
                      ['POST', '/upload', 'Upload PDF for background processing'],
                      ['GET', '/status/{submission_id}', 'Check processing status'],
                      ['GET', '/submission/{submission_id}', 'Get candidate results'],
                      ['POST', '/submission/{submission_id}/mark', 'Mark a stored submission'],
                      ['GET', '/submission/{submission_id}/json', 'Download raw JSON file'],
                      ['GET', '/submission/{submission_id}/logs', 'View processing logs'],
                      ['DELETE', '/submission/{submission_id}', 'Delete a submission'],
                      ['GET', '/submissions', 'List all submissions'],
                    ].map(([method, path, d], i) => (
                      <tr key={i} className="hover:bg-slate-50">
                        <td className="p-3">
                          <span className={`px-2 py-0.5 text-xs font-bold rounded ${method === 'GET' ? 'bg-green-100 text-green-700' : method === 'POST' ? 'bg-blue-100 text-blue-700' : 'bg-red-100 text-red-700'}`}>{method}</span>
                        </td>
                        <td className="p-3 font-mono text-xs">{path}</td>
                        <td className="p-3 text-slate-600">{d}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="mb-8">
              <h3 className="text-lg font-bold text-slate-800 mb-3">Candidate Object Schema</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm border border-slate-200 rounded-lg overflow-hidden">
                  <thead className="bg-slate-100">
                    <tr>
                      <th className="text-left p-3 font-semibold">Field</th>
                      <th className="text-left p-3 font-semibold">Type</th>
                      <th className="text-left p-3 font-semibold">Description</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200">
                    <tr><td className="p-3 font-mono text-xs">candidate_name</td><td className="p-3">string</td><td className="p-3">Full name of the candidate</td></tr>
                    <tr><td className="p-3 font-mono text-xs">candidate_number</td><td className="p-3">string</td><td className="p-3">Candidate ID / seat number</td></tr>
                    <tr><td className="p-3 font-mono text-xs">country</td><td className="p-3">string</td><td className="p-3">Country</td></tr>
                    <tr><td className="p-3 font-mono text-xs">paper_type</td><td className="p-3">string</td><td className="p-3">Paper type / level (e.g. &quot;PAPER A&quot;)</td></tr>
                    <tr><td className="p-3 font-mono text-xs">answers</td><td className="p-3">object</td><td className="p-3">{`{"1": "D", "2": "BL", "3": "IN"}`} &mdash; MCQ answers</td></tr>
                    <tr><td className="p-3 font-mono text-xs">drawing_questions</td><td className="p-3">object</td><td className="p-3">{`{"31": "student text"}`} &mdash; free response / drawing</td></tr>
                    <tr className="bg-blue-50"><td className="p-3 font-mono text-xs">marked_answers</td><td className="p-3">object</td><td className="p-3">{`{"1": "P", "2": "BL", "3": "A"}`} &mdash; marking result (marking endpoints only)</td></tr>
                    <tr className="bg-blue-50"><td className="p-3 font-mono text-xs">marked_drawing</td><td className="p-3">object</td><td className="p-3">{`{"31": "P"}`} &mdash; drawing marking result</td></tr>
                    <tr className="bg-blue-50"><td className="p-3 font-mono text-xs">score</td><td className="p-3">object</td><td className="p-3">{`{"correct": 25, "total": 30, "percentage": 83.3}`}</td></tr>
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-slate-500 mt-2">Blue rows only appear in responses from marking endpoints.</p>
            </div>

            <div className="mb-8">
              <h3 className="text-lg font-bold text-slate-800 mb-3">Answer &amp; Marking Codes</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="border border-slate-200 rounded-lg p-4">
                  <h4 className="font-semibold text-slate-700 mb-3">Extraction Codes (answers field)</h4>
                  <div className="space-y-2">
                    <div className="flex items-center gap-3"><span className="w-12 text-center font-mono text-sm font-bold bg-blue-100 text-blue-700 rounded px-2 py-1">A&ndash;D</span><span className="text-sm text-slate-600">Selected answer letter</span></div>
                    <div className="flex items-center gap-3"><span className="w-12 text-center font-mono text-sm font-bold bg-yellow-100 text-yellow-700 rounded px-2 py-1">BL</span><span className="text-sm text-slate-600">Blank &mdash; no answer given</span></div>
                    <div className="flex items-center gap-3"><span className="w-12 text-center font-mono text-sm font-bold bg-orange-100 text-orange-700 rounded px-2 py-1">IN</span><span className="text-sm text-slate-600">Invalid &mdash; multiple answers or unreadable</span></div>
                  </div>
                </div>
                <div className="border border-slate-200 rounded-lg p-4">
                  <h4 className="font-semibold text-slate-700 mb-3">Marking Codes (marked_answers field)</h4>
                  <div className="space-y-2">
                    <div className="flex items-center gap-3"><span className="w-12 text-center font-mono text-sm font-bold bg-green-100 text-green-700 rounded px-2 py-1">P</span><span className="text-sm text-slate-600">Correct answer (Pass)</span></div>
                    <div className="flex items-center gap-3"><span className="w-12 text-center font-mono text-sm font-bold bg-yellow-100 text-yellow-700 rounded px-2 py-1">BL</span><span className="text-sm text-slate-600">Blank &mdash; carried from extraction</span></div>
                    <div className="flex items-center gap-3"><span className="w-12 text-center font-mono text-sm font-bold bg-orange-100 text-orange-700 rounded px-2 py-1">IN</span><span className="text-sm text-slate-600">Invalid &mdash; carried from extraction</span></div>
                    <div className="flex items-center gap-3"><span className="w-12 text-center font-mono text-sm font-bold bg-red-100 text-red-700 rounded px-2 py-1">IM</span><span className="text-sm text-slate-600">Incorrect drawing / free-response</span></div>
                    <div className="flex items-center gap-3"><span className="w-12 text-center font-mono text-sm font-bold bg-slate-100 text-slate-700 rounded px-2 py-1">A&ndash;D</span><span className="text-sm text-slate-600">Wrong answer &mdash; shows student&#39;s letter</span></div>
                  </div>
                </div>
              </div>
            </div>

            <div className="mb-8">
              <h3 className="text-lg font-bold text-slate-800 mb-3">Error Responses</h3>
              <div className="space-y-3">
                <div className="border-l-4 border-red-500 bg-red-50 p-4">
                  <h4 className="font-bold text-red-900">400 Bad Request</h4>
                  <p className="text-red-700 text-sm">Invalid file format or request body.</p>
                  <pre className="text-xs mt-2 bg-red-100 p-2 rounded font-mono">{'{ "detail": "Only PDF files are allowed" }'}</pre>
                </div>
                <div className="border-l-4 border-orange-500 bg-orange-50 p-4">
                  <h4 className="font-bold text-orange-900">404 Not Found</h4>
                  <p className="text-orange-700 text-sm">Submission, answer key, or candidate results not found.</p>
                  <pre className="text-xs mt-2 bg-orange-100 p-2 rounded font-mono">{'{ "detail": "Submission not found" }'}</pre>
                </div>
                <div className="border-l-4 border-yellow-500 bg-yellow-50 p-4">
                  <h4 className="font-bold text-yellow-900">500 Internal Server Error</h4>
                  <p className="text-yellow-700 text-sm">Extraction or processing failed.</p>
                  <pre className="text-xs mt-2 bg-yellow-100 p-2 rounded font-mono">{'{ "detail": "Extraction failed: <error>" }'}</pre>
                </div>
              </div>
            </div>

            <div className="mb-8">
              <h3 className="text-lg font-bold text-slate-800 mb-3">Performance &amp; Limits</h3>
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-6">
                <ul className="space-y-2 text-slate-700">
                  <li className="flex items-start gap-2"><span className="text-blue-600 font-bold">&#x2022;</span><span><strong>Max File Size:</strong> 50 MB per PDF</span></li>
                  <li className="flex items-start gap-2"><span className="text-blue-600 font-bold">&#x2022;</span><span><strong>Processing Speed:</strong> ~1-3 seconds per page (parallel processing)</span></li>
                  <li className="flex items-start gap-2"><span className="text-blue-600 font-bold">&#x2022;</span><span><strong>Parallel Workers:</strong> Adaptive &mdash; up to 4 for small PDFs, 2 for large (&gt;20 pages)</span></li>
                  <li className="flex items-start gap-2"><span className="text-blue-600 font-bold">&#x2022;</span><span><strong>Supported Formats:</strong> PDF only</span></li>
                  <li className="flex items-start gap-2"><span className="text-blue-600 font-bold">&#x2022;</span><span><strong>Sync Endpoints:</strong> /extract/json and /extract/json/mark block until complete</span></li>
                  <li className="flex items-start gap-2"><span className="text-blue-600 font-bold">&#x2022;</span><span><strong>Async Endpoint:</strong> /upload returns immediately &mdash; poll /status/:id for results</span></li>
                </ul>
              </div>
            </div>

            <div className="mb-8">
              <h3 className="text-lg font-bold text-slate-800 mb-3">Best Practices</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="bg-green-50 border-l-4 border-green-500 p-4">
                  <h4 className="font-semibold text-green-900">&#x2713; Do&#39;s</h4>
                  <ul className="mt-2 space-y-1 text-green-800 text-sm">
                    <li>&#x2022; Use high-quality scanned PDFs (300+ DPI)</li>
                    <li>&#x2022; Store answer keys for reuse across batches</li>
                    <li>&#x2022; Use /upload for large batches, /extract/json for quick single files</li>
                    <li>&#x2022; Check the BL/IN counts to gauge scan quality</li>
                    <li>&#x2022; Implement retry logic for network failures</li>
                  </ul>
                </div>
                <div className="bg-red-50 border-l-4 border-red-500 p-4">
                  <h4 className="font-semibold text-red-900">&#x2717; Don&#39;ts</h4>
                  <ul className="mt-2 space-y-1 text-red-800 text-sm">
                    <li>&#x2022; Don&#39;t send low-resolution or blurry scans</li>
                    <li>&#x2022; Don&#39;t upload non-exam PDFs</li>
                    <li>&#x2022; Don&#39;t ignore IN (invalid) &mdash; review those manually</li>
                    <li>&#x2022; Don&#39;t hardcode answer keys &mdash; use the CRUD API</li>
                    <li>&#x2022; Don&#39;t skip error handling</li>
                  </ul>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="mt-10 bg-gradient-to-r from-blue-50 to-purple-50 border border-blue-200 rounded-lg p-6">
          <h2 className="text-xl font-bold text-slate-800 mb-3">Need Help?</h2>
          <p className="text-slate-600 mb-4">Explore the interactive OpenAPI docs or jump straight to the upload demo.</p>
          <div className="flex gap-3">
            <Link to="/" className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
              Try Upload Demo
            </Link>
            <a href={`${API_BASE}/docs`} target="_blank" rel="noopener noreferrer" className="px-4 py-2 bg-slate-200 text-slate-700 rounded-lg hover:bg-slate-300 transition-colors">
              OpenAPI (Swagger) Docs
            </a>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ApiDocsPage;