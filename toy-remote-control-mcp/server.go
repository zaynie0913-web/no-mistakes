package main

import (
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"sync"
	"time"
)

// JSON-RPC 2.0 types

type JSONRPCRequest struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      any             `json:"id,omitempty"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params,omitempty"`
}

type JSONRPCResponse struct {
	JSONRPC string        `json:"jsonrpc"`
	ID      any           `json:"id"`
	Result  any           `json:"result,omitempty"`
	Error   *JSONRPCError `json:"error,omitempty"`
}

type JSONRPCError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

// MCP protocol types

type ServerInfo struct {
	Name    string `json:"name"`
	Version string `json:"version"`
}

type InitializeResult struct {
	ProtocolVersion string     `json:"protocolVersion"`
	Capabilities    any        `json:"capabilities"`
	ServerInfo      ServerInfo `json:"serverInfo"`
}

type Tool struct {
	Name        string     `json:"name"`
	Description string     `json:"description"`
	InputSchema InputSchema `json:"inputSchema"`
}

type InputSchema struct {
	Type       string              `json:"type"`
	Properties map[string]Property `json:"properties,omitempty"`
	Required   []string            `json:"required,omitempty"`
}

type Property struct {
	Type        string   `json:"type"`
	Description string   `json:"description,omitempty"`
	Minimum     *int     `json:"minimum,omitempty"`
	Maximum     *int     `json:"maximum,omitempty"`
	Enum        []string `json:"enum,omitempty"`
	Items       *Items   `json:"items,omitempty"`
}

type Items struct {
	Type string `json:"type"`
}

type ToolCallParams struct {
	Name      string          `json:"name"`
	Arguments json.RawMessage `json:"arguments"`
}

type ToolResult struct {
	Content []ContentBlock `json:"content"`
	IsError bool           `json:"isError,omitempty"`
}

type ContentBlock struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

// Server

type Server struct {
	mu     sync.Mutex
	client *WSClient
	tools  []Tool
}

func NewServer() *Server {
	s := &Server{}
	s.tools = s.defineTools()
	return s
}

func (s *Server) Handle(req JSONRPCRequest) *JSONRPCResponse {
	switch req.Method {
	case "initialize":
		return s.handleInitialize(req)
	case "notifications/initialized":
		return nil
	case "tools/list":
		return s.handleToolsList(req)
	case "tools/call":
		return s.handleToolsCall(req)
	case "ping":
		return &JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{}}
	default:
		log.Printf("unknown method: %s", req.Method)
		return &JSONRPCResponse{
			JSONRPC: "2.0",
			ID:      req.ID,
			Error:   &JSONRPCError{Code: -32601, Message: "method not found"},
		}
	}
}

func (s *Server) handleInitialize(req JSONRPCRequest) *JSONRPCResponse {
	return &JSONRPCResponse{
		JSONRPC: "2.0",
		ID:      req.ID,
		Result: InitializeResult{
			ProtocolVersion: "2024-11-05",
			Capabilities:    map[string]any{"tools": map[string]any{}},
			ServerInfo:      ServerInfo{Name: "toy-remote-control", Version: "0.1.0"},
		},
	}
}

func (s *Server) handleToolsList(req JSONRPCRequest) *JSONRPCResponse {
	return &JSONRPCResponse{
		JSONRPC: "2.0",
		ID:      req.ID,
		Result:  map[string]any{"tools": s.tools},
	}
}

func (s *Server) handleToolsCall(req JSONRPCRequest) *JSONRPCResponse {
	var params ToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return &JSONRPCResponse{
			JSONRPC: "2.0",
			ID:      req.ID,
			Error:   &JSONRPCError{Code: -32602, Message: "invalid params"},
		}
	}

	result := s.dispatch(params.Name, params.Arguments)
	return &JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Result: result}
}

func (s *Server) dispatch(name string, args json.RawMessage) ToolResult {
	switch name {
	case "connect":
		return s.toolConnect(args)
	case "send":
		return s.toolSend(args)
	case "vibrate":
		return s.toolVibrate(args)
	case "stop":
		return s.toolStop(args)
	case "pattern":
		return s.toolPattern(args)
	case "status":
		return s.toolStatus(args)
	case "disconnect":
		return s.toolDisconnect(args)
	default:
		return textError("unknown tool: " + name)
	}
}

// Tool implementations

func (s *Server) toolConnect(args json.RawMessage) ToolResult {
	var p struct {
		URL              string            `json:"url"`
		Headers          map[string]string `json:"headers"`
		HandshakeMessage json.RawMessage   `json:"handshake_message"`
	}
	if err := json.Unmarshal(args, &p); err != nil {
		return textError("invalid arguments: " + err.Error())
	}
	if p.URL == "" {
		return textError("url is required")
	}

	s.mu.Lock()
	if s.client != nil {
		s.client.Close()
	}
	s.client = NewWSClient()
	s.mu.Unlock()

	if err := s.client.Connect(p.URL, p.Headers); err != nil {
		return textError("connection failed: " + err.Error())
	}

	if len(p.HandshakeMessage) > 0 {
		if err := s.client.SendRaw(p.HandshakeMessage); err != nil {
			return textError("connected but handshake failed: " + err.Error())
		}
	}

	return textOK("connected to " + p.URL)
}

func (s *Server) toolSend(args json.RawMessage) ToolResult {
	var p struct {
		Message json.RawMessage `json:"message"`
	}
	if err := json.Unmarshal(args, &p); err != nil {
		return textError("invalid arguments: " + err.Error())
	}

	s.mu.Lock()
	c := s.client
	s.mu.Unlock()

	if c == nil || !c.IsConnected() {
		return textError("not connected — call connect first")
	}

	if err := c.SendRaw(p.Message); err != nil {
		return textError("send failed: " + err.Error())
	}
	return textOK("sent")
}

func (s *Server) toolVibrate(args json.RawMessage) ToolResult {
	var p struct {
		Intensity   int `json:"intensity"`
		DurationSec int `json:"duration_sec"`
	}
	if err := json.Unmarshal(args, &p); err != nil {
		return textError("invalid arguments: " + err.Error())
	}
	if p.Intensity < 0 || p.Intensity > 20 {
		return textError("intensity must be 0-20")
	}

	s.mu.Lock()
	c := s.client
	s.mu.Unlock()

	if c == nil || !c.IsConnected() {
		return textError("not connected — call connect first")
	}

	cmd := map[string]any{
		"action":   "vibrate",
		"strength": p.Intensity,
		"timeSec":  p.DurationSec,
	}
	data, _ := json.Marshal(cmd)
	if err := c.SendRaw(data); err != nil {
		return textError("send failed: " + err.Error())
	}

	msg := fmt.Sprintf("vibrate intensity=%d", p.Intensity)
	if p.DurationSec > 0 {
		msg += fmt.Sprintf(" duration=%ds", p.DurationSec)
	}
	return textOK(msg)
}

func (s *Server) toolStop(args json.RawMessage) ToolResult {
	s.mu.Lock()
	c := s.client
	s.mu.Unlock()

	if c == nil || !c.IsConnected() {
		return textError("not connected — call connect first")
	}

	cmd := map[string]any{
		"action":   "vibrate",
		"strength": 0,
		"timeSec":  0,
	}
	data, _ := json.Marshal(cmd)
	if err := c.SendRaw(data); err != nil {
		return textError("send failed: " + err.Error())
	}
	return textOK("stopped")
}

func (s *Server) toolPattern(args json.RawMessage) ToolResult {
	var p struct {
		Steps []struct {
			Intensity  int `json:"intensity"`
			DurationMS int `json:"duration_ms"`
		} `json:"steps"`
		Repeat int `json:"repeat"`
	}
	if err := json.Unmarshal(args, &p); err != nil {
		return textError("invalid arguments: " + err.Error())
	}
	if len(p.Steps) == 0 {
		return textError("at least one step is required")
	}
	if p.Repeat <= 0 {
		p.Repeat = 1
	}

	s.mu.Lock()
	c := s.client
	s.mu.Unlock()

	if c == nil || !c.IsConnected() {
		return textError("not connected — call connect first")
	}

	go func() {
		for r := 0; r < p.Repeat; r++ {
			for _, step := range p.Steps {
				if !c.IsConnected() {
					return
				}
				intensity := step.Intensity
				if intensity < 0 {
					intensity = 0
				}
				if intensity > 20 {
					intensity = 20
				}
				cmd := map[string]any{
					"action":   "vibrate",
					"strength": intensity,
					"timeSec":  0,
				}
				data, _ := json.Marshal(cmd)
				if err := c.SendRaw(data); err != nil {
					log.Printf("pattern send error: %v", err)
					return
				}
				if step.DurationMS > 0 {
					time.Sleep(time.Duration(step.DurationMS) * time.Millisecond)
				}
			}
		}
	}()

	var parts []string
	for _, step := range p.Steps {
		parts = append(parts, fmt.Sprintf("%d@%dms", step.Intensity, step.DurationMS))
	}
	msg := fmt.Sprintf("pattern started: [%s] x%d", strings.Join(parts, ", "), p.Repeat)
	return textOK(msg)
}

func (s *Server) toolStatus(args json.RawMessage) ToolResult {
	s.mu.Lock()
	c := s.client
	s.mu.Unlock()

	if c == nil {
		return textOK("disconnected (no connection has been made)")
	}

	info := c.Info()
	status := "connected"
	if !info.Connected {
		status = "disconnected"
	}

	lines := []string{
		fmt.Sprintf("status: %s", status),
		fmt.Sprintf("url: %s", info.URL),
		fmt.Sprintf("connected_at: %s", info.ConnectedAt.Format(time.RFC3339)),
		fmt.Sprintf("messages_sent: %d", info.Sent),
		fmt.Sprintf("messages_received: %d", info.Received),
	}

	if len(info.RecentMessages) > 0 {
		lines = append(lines, "", "recent messages from toy:")
		for _, m := range info.RecentMessages {
			lines = append(lines, "  "+m)
		}
	}

	return textOK(strings.Join(lines, "\n"))
}

func (s *Server) toolDisconnect(args json.RawMessage) ToolResult {
	s.mu.Lock()
	c := s.client
	s.client = nil
	s.mu.Unlock()

	if c == nil {
		return textOK("already disconnected")
	}
	c.Close()
	return textOK("disconnected")
}

// Tool definitions

func (s *Server) defineTools() []Tool {
	intPtr := func(v int) *int { return &v }

	return []Tool{
		{
			Name:        "connect",
			Description: "Connect to the toy's control server via WebSocket. Use the URL and headers captured from your browser's DevTools Network tab.",
			InputSchema: InputSchema{
				Type: "object",
				Properties: map[string]Property{
					"url": {
						Type:        "string",
						Description: "WebSocket URL (wss://... or ws://...) captured from DevTools",
					},
					"headers": {
						Type:        "object",
						Description: "Optional HTTP headers for the connection (auth tokens, cookies, etc.)",
					},
					"handshake_message": {
						Type:        "object",
						Description: "Optional JSON message to send immediately after connecting (for authentication/room join)",
					},
				},
				Required: []string{"url"},
			},
		},
		{
			Name:        "send",
			Description: "Send a raw JSON message to the connected toy server. Use this when you know the exact protocol format from analyzing DevTools WebSocket frames.",
			InputSchema: InputSchema{
				Type: "object",
				Properties: map[string]Property{
					"message": {
						Type:        "object",
						Description: "The JSON message to send, matching the toy's protocol format",
					},
				},
				Required: []string{"message"},
			},
		},
		{
			Name:        "vibrate",
			Description: "Set vibration intensity. Uses a default message format — if the toy uses a different protocol, use the send tool with the correct format instead.",
			InputSchema: InputSchema{
				Type: "object",
				Properties: map[string]Property{
					"intensity": {
						Type:        "integer",
						Description: "Vibration intensity level (0 = off, 20 = max)",
						Minimum:     intPtr(0),
						Maximum:     intPtr(20),
					},
					"duration_sec": {
						Type:        "integer",
						Description: "Duration in seconds (0 = continuous until changed)",
						Minimum:     intPtr(0),
					},
				},
				Required: []string{"intensity"},
			},
		},
		{
			Name:        "stop",
			Description: "Stop all vibration immediately.",
			InputSchema: InputSchema{
				Type:       "object",
				Properties: map[string]Property{},
			},
		},
		{
			Name:        "pattern",
			Description: "Execute a vibration pattern — a sequence of intensity/duration steps. Runs asynchronously in the background.",
			InputSchema: InputSchema{
				Type: "object",
				Properties: map[string]Property{
					"steps": {
						Type:        "array",
						Description: "Array of {intensity, duration_ms} steps. Example: [{\"intensity\":5,\"duration_ms\":500},{\"intensity\":15,\"duration_ms\":300}]",
						Items:       &Items{Type: "object"},
					},
					"repeat": {
						Type:        "integer",
						Description: "Number of times to repeat the pattern (default: 1)",
						Minimum:     intPtr(1),
					},
				},
				Required: []string{"steps"},
			},
		},
		{
			Name:        "status",
			Description: "Get connection status, message counts, and recent messages received from the toy.",
			InputSchema: InputSchema{
				Type:       "object",
				Properties: map[string]Property{},
			},
		},
		{
			Name:        "disconnect",
			Description: "Close the WebSocket connection to the toy server.",
			InputSchema: InputSchema{
				Type:       "object",
				Properties: map[string]Property{},
			},
		},
	}
}

// Helpers

func textOK(msg string) ToolResult {
	return ToolResult{
		Content: []ContentBlock{{Type: "text", Text: msg}},
	}
}

func textError(msg string) ToolResult {
	return ToolResult{
		Content: []ContentBlock{{Type: "text", Text: msg}},
		IsError: true,
	}
}
