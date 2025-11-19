# Exam Answer Extractor - Frontend

Beautiful, modern React frontend for the PDF exam answer extraction system.

## 🎨 Features

- **Drag & Drop Upload** - Intuitive file upload with visual feedback
- **Real-time Status** - Live tracking of PDF processing
- **Beautiful UI** - Modern design with Tailwind CSS
- **Responsive** - Works on desktop, tablet, and mobile
- **Export Results** - Download extracted data as JSON
- **Component-based** - Easy to maintain and extend

## 🚀 Quick Start

### Prerequisites

- Node.js 18+ installed
- Backend API running on `http://localhost:8000`

### Installation

```bash
# Navigate to frontend directory
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev
```

The app will be available at `http://localhost:3000`

## 📁 Project Structure

```
frontend/
├── src/
│   ├── components/          # Reusable UI components
│   │   ├── common/          # Basic components (Button, Card, etc.)
│   │   ├── FileUpload/      # File upload component
│   │   ├── StatusTracker/   # Processing status tracker
│   │   └── ResultsDisplay/  # Results display component
│   ├── pages/               # Page components
│   │   ├── UploadPage.jsx   # Main upload page
│   │   └── TrackingPage.jsx # Status tracking & results
│   ├── services/            # API services
│   │   └── api.js           # API client
│   ├── App.jsx              # Main app component
│   ├── main.jsx             # App entry point
│   └── index.css            # Global styles
├── package.json             # Dependencies
├── vite.config.js           # Vite configuration
├── tailwind.config.js       # Tailwind CSS config
└── README.md                # This file
```

## 🎯 Usage

### 1. Upload PDF

1. Open `http://localhost:3000`
2. Drag and drop a PDF exam answer sheet
3. Or click "Browse Files" to select a file
4. Click "Start Extraction"

### 2. Track Progress

- You'll be automatically redirected to the tracking page
- Watch real-time status updates
- See extraction progress

### 3. View Results

- When processing completes, results appear automatically
- Multiple choice answers shown in a grid
- Free response answers displayed with full text
- Export results as JSON

## 🛠️ Development

### Available Scripts

```bash
# Start development server
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview

# Lint code
npm run lint
```

### Adding New Components

1. Create component folder in `src/components/`
2. Add `index.jsx` file
3. Export component
4. Import in parent component

Example:
```jsx
// src/components/MyComponent/index.jsx
import React from 'react';

const MyComponent = ({ children }) => {
  return <div>{children}</div>;
};

export default MyComponent;
```

## 🎨 Styling

Uses **Tailwind CSS** for styling:

- **Utility-first** - Compose styles with utility classes
- **Responsive** - Mobile-first responsive design
- **Customizable** - Extend in `tailwind.config.js`
- **Dark mode ready** - Easy to add dark mode support

### Custom Classes

Pre-defined classes in `index.css`:

```css
.card              /* White card with shadow */
.btn-primary       /* Primary button style */
.btn-secondary     /* Secondary button style */
.badge-success     /* Success badge */
.badge-error       /* Error badge */
.badge-warning     /* Warning badge */
.badge-info        /* Info badge */
```

## 🔌 API Integration

API client configured in `src/services/api.js`:

### Available Methods

```javascript
import examAPI from './services/api';

// Upload PDF
await examAPI.uploadPDF(file, onProgressCallback);

// Get status
await examAPI.getStatus(submissionId);

// Get results
await examAPI.getSubmission(submissionId);

// List all submissions
await examAPI.listSubmissions({ skip: 0, limit: 10 });

// Delete submission
await examAPI.deleteSubmission(submissionId);

// Health check
await examAPI.checkHealth();
```

## 🌐 Environment Variables

Create `.env` file:

```env
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

## 📦 Dependencies

### Core
- **React 18** - UI framework
- **React Router** - Navigation
- **Axios** - HTTP client
- **Vite** - Build tool

### UI
- **Tailwind CSS** - Styling
- **Lucide React** - Icons
- **clsx** - Conditional classes

## 🎭 Components

### Common Components

#### Button
```jsx
<Button variant="primary" loading={false}>
  Click Me
</Button>
```

#### Card
```jsx
<Card className="p-6">
  Content
</Card>
```

#### Badge
```jsx
<Badge variant="success">Completed</Badge>
```

#### LoadingSpinner
```jsx
<LoadingSpinner size="lg" text="Loading..." />
```

#### Alert
```jsx
<Alert type="error">Error message</Alert>
```

#### ProgressBar
```jsx
<ProgressBar progress={75} />
```

### Feature Components

#### FileUpload
```jsx
<FileUpload
  onFileSelect={handleFileSelect}
  onUpload={handleUpload}
  uploading={uploading}
  uploadProgress={progress}
/>
```

#### StatusTracker
```jsx
<StatusTracker
  submissionId={123}
  onComplete={handleComplete}
/>
```

#### ResultsDisplay
```jsx
<ResultsDisplay results={extractionResults} />
```

## 🚀 Deployment

### Build for Production

```bash
npm run build
```

Output in `dist/` folder.

### Deploy to Netlify

```bash
# Install Netlify CLI
npm install -g netlify-cli

# Deploy
netlify deploy --prod
```

### Deploy to Vercel

```bash
# Install Vercel CLI
npm install -g vercel

# Deploy
vercel --prod
```

### Deploy with Docker

Create `Dockerfile`:

```dockerfile
FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=0 /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

Build and run:
```bash
docker build -t exam-extractor-frontend .
docker run -p 80:80 exam-extractor-frontend
```

## 🎯 Future Enhancements

- [ ] Dark mode support
- [ ] Batch upload multiple PDFs
- [ ] Answer comparison feature
- [ ] PDF preview before upload
- [ ] Download as Excel/CSV
- [ ] User authentication
- [ ] Submission history
- [ ] Real-time notifications
- [ ] Mobile app (React Native)

## 🐛 Troubleshooting

### API Connection Issues

**Problem:** Cannot connect to backend
**Solution:** 
- Ensure backend is running on `http://localhost:8000`
- Check CORS settings in backend
- Verify `.env` has correct API URL

### Upload Fails

**Problem:** File upload returns error
**Solution:**
- Check file is PDF format
- Verify file size < 50MB
- Check backend logs for errors

### Build Errors

**Problem:** `npm run build` fails
**Solution:**
```bash
# Clean install
rm -rf node_modules package-lock.json
npm install
npm run build
```

## 📄 License

MIT License - Feel free to use in your projects!

## 🤝 Contributing

Contributions welcome! Key areas:

- UI/UX improvements
- New components
- Performance optimizations
- Bug fixes
- Documentation

---

**Built with ❤️ using React, Vite, and Tailwind CSS**
