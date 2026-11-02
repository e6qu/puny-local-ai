package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
)

// chatBodyBytes caps a chat request; a long conversation fits easily.
const chatBodyBytes = 64 << 20

func decodeJSON(r *http.Request, out any) error {
	data, err := readAll(r.Body, chatBodyBytes)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, out)
}

// postJSON posts body to url and returns the response, whatever its status,
// so the caller passes an error from the model server on as it is.
func postJSON(ctx context.Context, url string, body any) (*http.Response, error) {
	data, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	return http.DefaultClient.Do(request)
}
