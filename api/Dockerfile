# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the current directory contents (server.py, routine.py, etc.) into the container
COPY ..
# Make port 8000 available to the world outside this container
EXPOSE 7000

# Run the application using uvicorn (bypasses the __main__ block)
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7000"]
