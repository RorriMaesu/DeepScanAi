# DeepScanAi

A disk analysis tool with AI capabilities for understanding file content.

## Features

- Scan directories to analyze disk usage
- Visualize disk usage with interactive charts
- AI-powered content analysis using Gemini API
- File management capabilities (delete, open, etc.)
- Customizable interface with dark/light mode

## Requirements

- Python 3.8+
- Required packages:
  - customtkinter
  - humanize
  - matplotlib
  - python-dotenv
  - google-generativeai

## Setup

1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Create a `.env` file with your Gemini API key:
   ```
   GEMINI_API_KEY=your_api_key_here
   ```
4. Run the application: `python app.py`

## Usage

1. Click "Select Folder" to choose a directory to analyze
2. The application will scan the directory and display file information
3. Use the various buttons to interact with files and analyze content
4. Toggle between different views using the view buttons

## Support the Project

[![Buy Me A Coffee](static/capitalismsucksbutiamsuperpassionateaboutbeingabletoaffordfood.png)](https://buymeacoffee.com/rorrimaesu)

## License

MIT License
