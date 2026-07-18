import asyncio
import os
from pathlib import Path

class AsyncAudioWaveform:
    def __init__(self, exe_path: str = "audiowaveform.exe"):
        self.exe_path = exe_path

    async def generate(self, input_file: str, output_file: str, **kwargs):
        """
        Asynchronously generates a waveform image or data file from audio.
        
        Args:
            input_file (str): Path to input audio (.mp3, .wav, .flac, etc.)
            output_file (str): Path to output file (.png, .json, .dat, .wav)
            **kwargs: Maps directly to audiowaveform CLI arguments.
        """
        if not os.path.exists(input_file):
            return {"status": "error", "message": f"Input file not found: {input_file}"}

        # Base command
        command = [
            self.exe_path,
            "--quiet", # Disable stdout progress to prevent blocking pipes
            "-i", str(input_file),
            "-o", str(output_file)
        ]

        # Map Python kwargs to CLI arguments
        for key, value in kwargs.items():
            # Convert python_snake_case to cli-kebab-case
            cli_flag = f"--{key.replace('_', '-')}"
            
            if isinstance(value, bool):
                # Handle boolean flags like --split-channels or --no-axis-labels
                if value:
                    command.append(cli_flag)
            else:
                # Handle standard key-value arguments
                command.extend([cli_flag, str(value)])

        try:
            # Execute non-blocking subprocess
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Wait for it to finish without blocking the main event loop
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                return {
                    "status": "error", 
                    "message": f"Process failed with code {process.returncode}",
                    "details": stderr.decode().strip()
                }

            return {
                "status": "success", 
                "data": {"output": output_file, "size_bytes": os.path.getsize(output_file)}
            }

        except FileNotFoundError:
            return {"status": "error", "message": f"Executable not found: {self.exe_path}"}
        except Exception as e:
            return {"status": "error", "message": f"Unexpected error: {e}"}

    # ==========================================
    # Convenience Methods for Common Workflows
    # ==========================================

    async def extract_json_data(self, input_file: str, output_file: str, pixels_per_second: int = 100):
        """Extracts waveform peak data to a JSON array (great for web UI rendering)."""
        return await self.generate(
            input_file, 
            output_file, 
            pixels_per_second=pixels_per_second,
            bits=8 # 8-bit is usually plenty of resolution for UI data and saves space
        )

    async def create_themed_image(self, input_file: str, output_file: str, width: int = 800, height: int = 250):
        """Generates a styled PNG waveform graphic."""
        return await self.generate(
            input_file, 
            output_file,
            width=width,
            height=height,
            waveform_style="bars",
            bar_style="rounded",
            bar_width=4,
            bar_gap=2,
            background_color="0B0B10ff", 
            waveform_color="7A2CFFff",   
            border_color="1E1B2Eff",     
            axis_label_color="B8B6CCff", 
            with_axis_labels=True
        )

# ==========================================
# Example Usage (Concurrent Execution)
# ==========================================
async def main():
    waveform_gen = AsyncAudioWaveform(exe_path="audiowaveform.exe")
    
    # Let's say we have an uploaded podcast file
    audio_track = "interview_raw.mp3"
    
    # We want to do two things at the exact same time without freezing the app:
    # 1. Generate a JSON file of the peaks to feed to a frontend React player.
    # 2. Generate a styled PNG image to use as the track's preview thumbnail.
    
    print("Starting concurrent waveform generation...")
    
    

# [Image of an audio waveform graph]

    
    # Run both intensive tasks concurrently
    json_task = waveform_gen.extract_json_data(audio_track, "track_data.json")
    image_task = waveform_gen.create_themed_image(audio_track, "track_preview.png")
    
    results = await asyncio.gather(json_task, image_task)
    
    print("\n--- Results ---")
    print(f"JSON Data: {results[0]}")
    print(f"PNG Image: {results[1]}")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # Assuming 'interview_raw.mp3' exists in the directory, otherwise this will gracefully return the file-not-found error
    # asyncio.run(main())