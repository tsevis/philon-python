import AppKit
import Foundation
import Vision

struct Line: Encodable {
    let text: String
    let confidence: Float
    let bbox: [Float]
}

struct Response: Encodable {
    let lines: [Line]
}

guard CommandLine.arguments.count >= 2 else {
    FileHandle.standardError.write(Data("Usage: philon-vision <image-path> [fast|accurate]\n".utf8))
    exit(64)
}

let imageURL = URL(fileURLWithPath: CommandLine.arguments[1])
let mode = CommandLine.arguments.dropFirst(2).first ?? "accurate"
let request = VNRecognizeTextRequest()
request.recognitionLevel = mode == "fast" ? .fast : .accurate
request.usesLanguageCorrection = mode != "fast"
// Keep recognition local while allowing mixed-language research documents.
// The engine can later pass document-level language hints to a model pack,
// but the baseline Vision recognizer should not force an English-only path.
request.automaticallyDetectsLanguage = true

do {
    // Load the source into a CGImage before passing it to Vision. The URL
    // request handler can fail with a non-diagnostic Objective-C error for
    // otherwise valid local files on macOS when the Vision service resolves
    // a temporary path itself.
    guard let image = NSImage(contentsOf: imageURL),
          let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        throw NSError(domain: "io.philon.vision", code: 1, userInfo: [NSLocalizedDescriptionKey: "The input could not be decoded as an image."])
    }
    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try handler.perform([request])
    let lines = (request.results ?? []).compactMap { observation -> Line? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return Line(text: candidate.string, confidence: candidate.confidence, bbox: [Float(box.origin.x), Float(box.origin.y), Float(box.width), Float(box.height)])
    }
    let encoded = try JSONEncoder().encode(Response(lines: lines))
    FileHandle.standardOutput.write(encoded)
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    FileHandle.standardError.write(Data("Vision OCR failed: \(error)\n".utf8))
    exit(1)
}
