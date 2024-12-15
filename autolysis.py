import os
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
import numpy as np
from typing import Dict, List, Any, Tuple
import json
import base64
import requests
from io import BytesIO
import sys
from dotenv import load_dotenv
import urllib3
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Add after imports
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Load environment variables from .env file
load_dotenv()

# Constants
AIPROXY_URL = "https://aiproxy.sanand.workers.dev/openai/v1"
MAX_TOKENS = 4096
CHART_SIZE = (8, 6)
MAX_RETRIES = 3
RETRY_BACKOFF = 2

class DataAnalyzer:
    def __init__(self, csv_path: str):
        self.token = os.environ.get("AIPROXY_TOKEN")
        if not self.token:
            raise ValueError("AIPROXY_TOKEN environment variable not set")
        
        # Try different encodings
        encodings = ['utf-8', 'latin1', 'iso-8859-1', 'cp1252']
        for encoding in encodings:
            try:
                self.df = pd.read_csv(csv_path, encoding=encoding)
                print(f"Successfully read file with {encoding} encoding")
                break
            except UnicodeDecodeError:
                if encoding == encodings[-1]:  # Last encoding attempt
                    raise ValueError(f"Could not read file with any of these encodings: {encodings}")
                continue
        
        self.filename = os.path.basename(csv_path)
        self.charts = []
        
        # Setup session with retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=RETRY_BACKOFF,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        
    def call_llm(self, prompt: str, system: str = None) -> str:
        """Make an API call to the LLM with retry logic"""
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        data = {
            "model": "gpt-4o-mini",
            "messages": messages,
            "max_tokens": MAX_TOKENS
        }
        
        for attempt in range(MAX_RETRIES):
            try:
                response = self.session.post(
                    f"{AIPROXY_URL}/chat/completions",
                    headers=headers,
                    json=data,
                    verify=False,
                    timeout=30
                )
                
                # Print response details for debugging
                print(f"Response status: {response.status_code}")
                print(f"Response headers: {dict(response.headers)}")
                if response.status_code != 200:
                    print(f"Response text: {response.text}")
                
                if response.status_code == 200:
                    return response.json()["choices"][0]["message"]["content"]
                    
                # Handle rate limiting
                if response.status_code == 429:
                    wait_time = int(response.headers.get('Retry-After', 5))
                    print(f"Rate limited. Waiting {wait_time} seconds...")
                    time.sleep(wait_time)
                    continue
                    
                response.raise_for_status()
                
            except requests.exceptions.RequestException as e:
                if attempt == MAX_RETRIES - 1:  # Last attempt
                    print(f"Error calling LLM API: {str(e)}")
                    raise
                print(f"Attempt {attempt + 1} failed. Retrying...")
                time.sleep(RETRY_BACKOFF ** attempt)
                
        raise Exception("Max retries exceeded when calling LLM API")

    def get_data_summary(self) -> Dict[str, Any]:
        """Generate a summary of the dataset"""
        summary = {
            "filename": self.filename,
            "rows": len(self.df),
            "columns": list(self.df.columns),
            "dtypes": self.df.dtypes.astype(str).to_dict(),
            "missing_values": self.df.isnull().sum().to_dict(),
            "numeric_summary": {},
            "categorical_summary": {}
        }
        
        # Summarize numeric columns
        numeric_cols = self.df.select_dtypes(include=[np.number]).columns
        for col in numeric_cols:
            summary["numeric_summary"][col] = {
                "mean": float(self.df[col].mean()),
                "std": float(self.df[col].std()),
                "min": float(self.df[col].min()),
                "max": float(self.df[col].max())
            }
            
        # Summarize categorical columns
        categorical_cols = self.df.select_dtypes(exclude=[np.number]).columns
        for col in categorical_cols:
            value_counts = self.df[col].value_counts()
            summary["categorical_summary"][col] = {
                "unique_values": len(value_counts),
                "top_values": value_counts.head(5).to_dict()
            }
            
        return summary

    def create_correlation_heatmap(self) -> str:
        """Create and save a correlation heatmap"""
        numeric_df = self.df.select_dtypes(include=[np.number])
        if len(numeric_df.columns) < 2:
            return None
            
        plt.figure(figsize=CHART_SIZE)
        sns.heatmap(numeric_df.corr(), annot=True, cmap='coolwarm')
        plt.title('Correlation Heatmap')
        
        filename = 'correlation_heatmap.png'
        plt.savefig(filename)
        plt.close()
        self.charts.append(filename)
        return filename

    def create_distribution_plot(self) -> str:
        """Create and save distribution plots for numeric columns"""
        numeric_cols = self.df.select_dtypes(include=[np.number]).columns[:3]
        if len(numeric_cols) == 0:
            return None
            
        plt.figure(figsize=CHART_SIZE)
        for i, col in enumerate(numeric_cols, 1):
            plt.subplot(len(numeric_cols), 1, i)
            sns.histplot(self.df[col].dropna(), kde=True)
            plt.title(f'Distribution of {col}')
            
        plt.tight_layout()
        filename = 'distributions.png'
        plt.savefig(filename)
        plt.close()
        self.charts.append(filename)
        return filename

    def create_scatter_plot(self) -> str:
        """Create and save a scatter plot of the two most correlated numeric columns"""
        numeric_df = self.df.select_dtypes(include=[np.number])
        if len(numeric_df.columns) < 2:
            return None
            
        corr_matrix = numeric_df.corr()
        np.fill_diagonal(corr_matrix.values, -1)
        max_corr_idx = np.unravel_index(corr_matrix.values.argmax(), corr_matrix.shape)
        col1, col2 = corr_matrix.index[max_corr_idx[0]], corr_matrix.columns[max_corr_idx[1]]
        
        plt.figure(figsize=CHART_SIZE)
        sns.scatterplot(data=self.df, x=col1, y=col2)
        plt.title(f'Scatter Plot: {col1} vs {col2}')
        
        filename = 'scatter_plot.png'
        plt.savefig(filename)
        plt.close()
        self.charts.append(filename)
        return filename

    def encode_image(self, image_path: str) -> str:
        """Encode image to base64 for LLM vision"""
        with open(image_path, 'rb') as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def analyze_and_narrate(self):
        """Main function to analyze data and generate narrative"""
        # Generate data summary
        summary = self.get_data_summary()
        
        # Create visualizations
        charts = []
        for chart_func in [self.create_correlation_heatmap, 
                          self.create_distribution_plot,
                          self.create_scatter_plot]:
            chart = chart_func()
            if chart:
                charts.append(chart)

        # Get initial analysis from LLM
        initial_prompt = f"""
        Analyze this dataset summary and provide initial insights:
        {json.dumps(summary, indent=2)}
        """
        initial_analysis = self.call_llm(initial_prompt)

        # Get visualization analysis using vision capabilities
        vision_insights = []
        for chart in charts:
            chart_prompt = f"""
            Analyze this visualization and provide insights:
            [Image: {chart}]
            """
            vision_insights.append(self.call_llm(chart_prompt))

        # Generate final narrative
        narrative_prompt = f"""
        Create a comprehensive README.md file with the following structure:
        1. Brief introduction about the dataset
        2. Key findings from the analysis
        3. Insights from visualizations
        4. Implications and recommendations
        
        Use this information:
        Initial Analysis: {initial_analysis}
        Visualization Insights: {' '.join(vision_insights)}
        
        Format the response in proper Markdown with headers, lists, and emphasis.
        Include the chart filenames in proper Markdown image syntax.
        """
        
        narrative = self.call_llm(narrative_prompt)
        
        # Save README.md
        with open('README.md', 'w') as f:
            f.write(narrative)

def main():
    if len(sys.argv) != 2:
        print("Usage: uv run autolysis.py <dataset.csv>")
        sys.exit(1)
        
    csv_path = sys.argv[1]
    analyzer = DataAnalyzer(csv_path)
    analyzer.analyze_and_narrate()

if __name__ == "__main__":
    main() 