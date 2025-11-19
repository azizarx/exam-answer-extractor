# 📚 Documentation Index

Welcome! This is your guide to all documentation for the Exam Answer Sheet Extraction System.

## 🚀 Quick Start (Start Here!)

**New to the project? Start with these:**

1. **[GETTING_STARTED.md](GETTING_STARTED.md)** ⭐ **START HERE**
   - Fastest way to get running (5 minutes setup)
   - Step-by-step for beginners
   - Common issues & solutions
   - Quick command reference

2. **[QUICKSTART.md](QUICKSTART.md)**
   - Alternative quick start guide
   - Detailed installation steps
   - Configuration examples
   - Testing instructions

## 📖 Main Documentation

3. **[README.md](README.md)** - Main Documentation
   - Complete feature overview
   - Full tech stack details
   - API usage examples
   - Database schema
   - Deployment guides

4. **[ARCHITECTURE.md](ARCHITECTURE.md)** - System Design
   - System architecture diagrams
   - Component interactions
   - Data flow explanations
   - Service layer details
   - Design decisions

5. **[PROJECT_SUMMARY.md](PROJECT_SUMMARY.md)** - Project Overview
   - Executive summary
   - File structure
   - Key components explained
   - Development workflow

## 🖥️ Platform-Specific Guides

6. **[WINDOWS_SETUP.md](WINDOWS_SETUP.md)** - Windows Users
   - Windows-specific installation
   - PowerShell commands
   - Common Windows issues
   - Path configuration
   - Service management

## 🎨 Frontend Documentation

7. **[frontend/README.md](frontend/README.md)** - Frontend Guide
   - React component structure
   - Available components
   - Styling guide
   - Development workflow
   - Component API reference

## 🎯 Quick Reference by Task

### "I want to..."

#### Get Started
→ Read [GETTING_STARTED.md](GETTING_STARTED.md) first  
→ Then follow [QUICKSTART.md](QUICKSTART.md)

#### Install on Windows
→ See [WINDOWS_SETUP.md](WINDOWS_SETUP.md)

#### Understand the System
→ Read [ARCHITECTURE.md](ARCHITECTURE.md)  
→ Review [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md)

#### Use the API
→ Main docs: [README.md](README.md) (API Usage section)  
→ Live docs: http://localhost:8000/docs (when running)

#### Customize the Frontend
→ See [frontend/README.md](frontend/README.md)  
→ Check `frontend/src/components/` for examples

#### Deploy to Production
→ [README.md](README.md) (Deployment section)  
→ [ARCHITECTURE.md](ARCHITECTURE.md) (Production considerations)

#### Troubleshoot Issues
→ [GETTING_STARTED.md](GETTING_STARTED.md) (Common Issues section)  
→ [WINDOWS_SETUP.md](WINDOWS_SETUP.md) (Troubleshooting section)

## 📂 Documentation Structure

```
Project1/
├── INDEX.md                    # 👈 You are here
├── GETTING_STARTED.md          # ⭐ Start here!
├── QUICKSTART.md               # Quick installation
├── README.md                   # Main documentation
├── ARCHITECTURE.md             # System design
├── PROJECT_SUMMARY.md          # Project overview
├── WINDOWS_SETUP.md            # Windows guide
└── frontend/
    └── README.md               # Frontend guide
```

## 🎓 Learning Path

**For Beginners:**
1. [GETTING_STARTED.md](GETTING_STARTED.md) - Get it running
2. [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) - Understand what it does
3. [README.md](README.md) - Learn all features
4. [ARCHITECTURE.md](ARCHITECTURE.md) - Deep dive into design

**For Developers:**
1. [QUICKSTART.md](QUICKSTART.md) - Quick setup
2. [ARCHITECTURE.md](ARCHITECTURE.md) - System design
3. [frontend/README.md](frontend/README.md) - Frontend components
4. API docs at http://localhost:8000/docs

**For Windows Users:**
1. [WINDOWS_SETUP.md](WINDOWS_SETUP.md) - Windows-specific setup
2. [GETTING_STARTED.md](GETTING_STARTED.md) - General guide
3. [README.md](README.md) - Full documentation

## 🔧 Quick Commands

```powershell
# Start the full stack (backend + frontend)
.\start-fullstack.ps1

# Start backend only
.\start.ps1

# Start frontend only
cd frontend
.\start.ps1

# View this index
notepad INDEX.md
```

## 🌐 Access Points

Once running:

- **Frontend Web App**: http://localhost:3000
- **Backend API**: http://localhost:8000
- **API Documentation**: http://localhost:8000/docs
- **Alternative API Docs**: http://localhost:8000/redoc

## 📦 What Each Document Contains

| Document | Purpose | Read Time | Best For |
|----------|---------|-----------|----------|
| GETTING_STARTED.md | Quick setup guide | 5 min | First-time users |
| QUICKSTART.md | Installation guide | 10 min | Setup help |
| README.md | Complete reference | 20 min | Understanding everything |
| ARCHITECTURE.md | System design | 15 min | Developers |
| PROJECT_SUMMARY.md | Overview | 5 min | Quick understanding |
| WINDOWS_SETUP.md | Windows guide | 15 min | Windows users |
| frontend/README.md | Frontend guide | 10 min | Frontend developers |

## 🎯 Most Common Questions

**Q: How do I start the system?**  
A: Run `.\start-fullstack.ps1` → See [GETTING_STARTED.md](GETTING_STARTED.md)

**Q: What API endpoints are available?**  
A: See [README.md](README.md) or visit http://localhost:8000/docs

**Q: How does the extraction work?**  
A: See [ARCHITECTURE.md](ARCHITECTURE.md) - Extraction Pipeline section

**Q: How do I customize the UI?**  
A: See [frontend/README.md](frontend/README.md)

**Q: I'm on Windows and having issues**  
A: See [WINDOWS_SETUP.md](WINDOWS_SETUP.md) - Troubleshooting section

**Q: How is the data stored?**  
A: See [ARCHITECTURE.md](ARCHITECTURE.md) - Data Flow section

## 🆘 Need Help?

1. **Check relevant documentation above**
2. **Review [GETTING_STARTED.md](GETTING_STARTED.md) - Common Issues**
3. **Check API docs** at http://localhost:8000/docs
4. **Enable debug logging** in `.env`:
   ```env
   LOG_LEVEL=DEBUG
   ```

## 🚀 Ready to Start?

👉 Open [GETTING_STARTED.md](GETTING_STARTED.md) and follow the guide!

---

*Last Updated: 2025*  
*Project: Exam Answer Sheet Extraction System*  
*Tech Stack: FastAPI, React, PostgreSQL, OpenAI GPT-4 Vision, DigitalOcean Spaces*
