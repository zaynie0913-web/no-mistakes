package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"log"
	"os"
)

func main() {
	log.SetOutput(os.Stderr)
	server := NewServer()

	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 0, 4*1024*1024), 4*1024*1024)

	for scanner.Scan() {
		var msg JSONRPCRequest
		if err := json.Unmarshal(scanner.Bytes(), &msg); err != nil {
			log.Printf("invalid message: %v", err)
			continue
		}

		if resp := server.Handle(msg); resp != nil {
			out, _ := json.Marshal(resp)
			fmt.Fprintf(os.Stdout, "%s\n", out)
		}
	}
	if err := scanner.Err(); err != nil {
		log.Fatalf("stdin read error: %v", err)
	}
}
