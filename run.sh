

# 3. Start the server
./run.sh dev

# 4. Open http://localhost:5000 in browser

# 5. Test the API
curl -X POST http://localhost:5000/api/generate \
  -H "Content-Type: application/json" \
  -d '{"story":"Launching our AI tool...","platforms":["facebook","instagram"]}'