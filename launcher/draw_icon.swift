// Draws the "First Basket Picks" app icon (1024×1024 PNG) — "on fire" style: a flaming basketball dropping into a
// white-hot rim, flames roaring up behind it, embers in a dark red arena, cyan edge light on the ball.
// Usage:  swift draw_icon.swift out.png
import AppKit

let S: CGFloat = 1024
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon_1024.png"
let space = CGColorSpaceCreateDeviceRGB()
let ctx = CGContext(data: nil, width: Int(S), height: Int(S), bitsPerComponent: 8, bytesPerRow: 0,
                    space: space, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!

func rgba(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat = 1) -> CGColor {
    CGColor(red: r / 255, green: g / 255, blue: b / 255, alpha: a)
}
func grad(_ colors: [CGColor], _ locs: [CGFloat]) -> CGGradient {
    CGGradient(colorsSpace: space, colors: colors as CFArray, locations: locs)!
}
func lerp(_ a: CGFloat, _ b: CGFloat, _ t: CGFloat) -> CGFloat { a + (b - a) * t }
var seed: UInt64 = 20261118                                  // fixed seed: the same icon every time
func rnd() -> CGFloat { seed = seed &* 6364136223846793005 &+ 1442695040888963407; return CGFloat(seed >> 33) / CGFloat(1 << 31) }

// ── Tile ────────────────────────────────────────────────────────────────────────────────
let tile = CGRect(x: 100, y: 100, width: 824, height: 824)
let tilePath = CGPath(roundedRect: tile, cornerWidth: 185, cornerHeight: 185, transform: nil)
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -16), blur: 34, color: rgba(0, 0, 0, 0.55))
ctx.addPath(tilePath); ctx.setFillColor(rgba(8, 4, 6)); ctx.fillPath()
ctx.restoreGState()

ctx.saveGState()
ctx.addPath(tilePath); ctx.clip()

// dark arena lit red-orange by the fire
ctx.drawRadialGradient(grad([rgba(120, 28, 8), rgba(52, 10, 8), rgba(12, 5, 8)], [0, 0.45, 1]),
                       startCenter: CGPoint(x: 512, y: 640), startRadius: 0,
                       endCenter: CGPoint(x: 512, y: 600), endRadius: 640, options: [.drawsAfterEndLocation])
// cool cyan fill light from the lower right (teal-and-orange look)
ctx.drawRadialGradient(grad([rgba(0, 200, 255, 0.28), rgba(0, 200, 255, 0)], [0, 1]),
                       startCenter: CGPoint(x: 920, y: 180), startRadius: 0,
                       endCenter: CGPoint(x: 920, y: 180), endRadius: 520, options: [])

// ── Flames rising from the ball ─────────────────────────────────────────────────────────
let c = CGPoint(x: 512, y: 590), r: CGFloat = 196            // the ball

func tongue(base: CGPoint, angle: CGFloat, length: CGFloat, width: CGFloat, wiggle: CGFloat) -> CGPath {
    // a flame tongue: wide at the base, curling to a sharp tip
    let dir = CGPoint(x: cos(angle), y: sin(angle)), nrm = CGPoint(x: -dir.y, y: dir.x)
    let tip = CGPoint(x: base.x + dir.x * length + nrm.x * wiggle, y: base.y + dir.y * length + nrm.y * wiggle)
    let l = CGPoint(x: base.x + nrm.x * width / 2, y: base.y + nrm.y * width / 2)
    let rr = CGPoint(x: base.x - nrm.x * width / 2, y: base.y - nrm.y * width / 2)
    let p = CGMutablePath()
    p.move(to: l)
    p.addCurve(to: tip,
               control1: CGPoint(x: l.x + dir.x * length * 0.45 + nrm.x * width * 0.35, y: l.y + dir.y * length * 0.45 + nrm.y * width * 0.35),
               control2: CGPoint(x: tip.x - dir.x * length * 0.30 - nrm.x * wiggle * 0.9, y: tip.y - dir.y * length * 0.30 - nrm.y * wiggle * 0.9))
    p.addCurve(to: rr,
               control1: CGPoint(x: tip.x - dir.x * length * 0.35 + nrm.x * wiggle * 0.2, y: tip.y - dir.y * length * 0.35 + nrm.y * wiggle * 0.2),
               control2: CGPoint(x: rr.x + dir.x * length * 0.40 - nrm.x * width * 0.30, y: rr.y + dir.y * length * 0.40 - nrm.y * width * 0.30))
    p.closeSubpath()
    return p
}
func drawFlames(layer: Int) {
    // three passes: dark-red outer flames, orange middle, yellow-white core
    let palettes: [[CGColor]] = [
        [rgba(255, 70, 10, 0.95), rgba(200, 20, 5, 0.55), rgba(120, 10, 5, 0)],
        [rgba(255, 170, 40, 1.0), rgba(255, 90, 15, 0.75), rgba(230, 40, 10, 0)],
        [rgba(255, 250, 215, 1.0), rgba(255, 210, 90, 0.85), rgba(255, 140, 30, 0)],
    ]
    let scale: CGFloat = [1.0, 0.72, 0.45][layer]
    let count = [17, 14, 10][layer]
    for i in 0..<count {
        let t = CGFloat(i) / CGFloat(count - 1)                     // spread across the ball's upper half
        let a = lerp(.pi * 0.10, .pi * 0.90, t)                    // where on the ball the tongue starts
        let base = CGPoint(x: c.x + cos(a) * r * 0.88, y: c.y + sin(a) * r * 0.70)
        let up = .pi / 2 + (t - 0.5) * 0.55 + (rnd() - 0.5) * 0.22  // fan out a little, mostly upwards
        let middle = 1 - abs(t - 0.5) * 1.3                         // taller flames in the middle
        let len = (230 + 260 * middle + rnd() * 70) * scale
        let w = (82 + rnd() * 34) * (0.6 + scale * 0.4)
        let path = tongue(base: base, angle: up, length: len, width: w, wiggle: (rnd() - 0.5) * 130 * scale)
        ctx.saveGState()
        ctx.addPath(path); ctx.clip()
        let tip = CGPoint(x: base.x + cos(up) * len, y: base.y + sin(up) * len)
        ctx.drawLinearGradient(grad(palettes[layer], [0, 0.55, 1]), start: base, end: tip,
                               options: [.drawsBeforeStartLocation])
        ctx.restoreGState()
    }
}
ctx.saveGState()
ctx.setShadow(offset: .zero, blur: 60, color: rgba(255, 90, 20, 0.9))       // heat glow around the fire
ctx.beginTransparencyLayer(auxiliaryInfo: nil)
drawFlames(layer: 0)
ctx.endTransparencyLayer()
ctx.restoreGState()
ctx.saveGState(); ctx.setBlendMode(.screen); drawFlames(layer: 1); ctx.restoreGState()
ctx.saveGState(); ctx.setBlendMode(.screen); drawFlames(layer: 2); ctx.restoreGState()

// ── Hoop (below the ball): glowing rim + net ────────────────────────────────────────────
let rimC = CGPoint(x: 512, y: 300), rimW: CGFloat = 470, rimH: CGFloat = 104
// net: stretched mesh hanging from the rim
ctx.saveGState()
ctx.setShadow(offset: .zero, blur: 10, color: rgba(255, 255, 255, 0.5))
ctx.setStrokeColor(rgba(255, 245, 235, 0.85)); ctx.setLineWidth(7); ctx.setLineCap(.round)
let netTop = rimC.y - 8, netBottom: CGFloat = 70, topHalf = rimW / 2 - 14, botHalf: CGFloat = 120
let strands = 9
for i in 0...strands {
    let t = CGFloat(i) / CGFloat(strands)
    let x0 = lerp(rimC.x - topHalf, rimC.x + topHalf, t)
    let xa = lerp(rimC.x - botHalf, rimC.x + botHalf, max(0, t - 0.5 / CGFloat(strands)))
    let xb = lerp(rimC.x - botHalf, rimC.x + botHalf, min(1, t + 0.5 / CGFloat(strands)))
    ctx.move(to: CGPoint(x: x0, y: netTop)); ctx.addLine(to: CGPoint(x: xa, y: netBottom)); ctx.strokePath()
    ctx.move(to: CGPoint(x: x0, y: netTop)); ctx.addLine(to: CGPoint(x: xb, y: netBottom)); ctx.strokePath()
}
for k in 1...2 {
    let t = CGFloat(k) / 3
    let y = lerp(netTop, netBottom, t), half = lerp(topHalf, botHalf, t)
    ctx.addEllipse(in: CGRect(x: rimC.x - half, y: y - 16, width: 2 * half, height: 32)); ctx.strokePath()
}
ctx.restoreGState()

// back half of the rim (behind the ball)
func rimPass(front: Bool) {
    ctx.saveGState()
    let clipRect = front ? CGRect(x: 0, y: 0, width: S, height: rimC.y) : CGRect(x: 0, y: rimC.y, width: S, height: S)
    ctx.clip(to: clipRect)
    let rect = CGRect(x: rimC.x - rimW / 2, y: rimC.y - rimH / 2, width: rimW, height: rimH)
    ctx.setShadow(offset: .zero, blur: 40, color: rgba(255, 110, 20, 1))
    ctx.setStrokeColor(rgba(255, 92, 14)); ctx.setLineWidth(26); ctx.strokeEllipse(in: rect)
    ctx.setShadow(offset: .zero, blur: 0, color: nil)
    ctx.setStrokeColor(rgba(255, 236, 200)); ctx.setLineWidth(8); ctx.strokeEllipse(in: rect)
    ctx.restoreGState()
}
rimPass(front: false)
ctx.restoreGState()

// ── Basketball ──────────────────────────────────────────────────────────────────────────
let ballRect = CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r)
ctx.saveGState()
ctx.setShadow(offset: .zero, blur: 70, color: rgba(255, 120, 30, 0.95))
ctx.setFillColor(rgba(230, 84, 12)); ctx.fillEllipse(in: ballRect)
ctx.restoreGState()
ctx.saveGState()
ctx.addEllipse(in: ballRect); ctx.clip()
ctx.drawRadialGradient(grad([rgba(255, 214, 150), rgba(255, 136, 36), rgba(214, 72, 10), rgba(96, 24, 6)],
                            [0, 0.30, 0.72, 1]),
                       startCenter: CGPoint(x: c.x - 70, y: c.y + 85), startRadius: 0,
                       endCenter: c, endRadius: r * 1.04, options: [.drawsAfterEndLocation])
// pebble texture
for _ in 0..<1400 {
    let a = rnd() * 2 * .pi, d = sqrt(rnd()) * r
    let p = CGPoint(x: c.x + cos(a) * d, y: c.y + sin(a) * d)
    let s = 1.5 + rnd() * 1.5
    ctx.setFillColor(rgba(90, 26, 4, 0.07 + rnd() * 0.07))
    ctx.fillEllipse(in: CGRect(x: p.x - s, y: p.y - s, width: 2 * s, height: 2 * s))
}
// seams
ctx.translateBy(x: c.x, y: c.y); ctx.rotate(by: 0.42)
ctx.setStrokeColor(rgba(36, 12, 4, 0.95)); ctx.setLineWidth(13); ctx.setLineCap(.round)
ctx.move(to: CGPoint(x: -r, y: 0)); ctx.addLine(to: CGPoint(x: r, y: 0)); ctx.strokePath()
ctx.move(to: CGPoint(x: 0, y: -r)); ctx.addLine(to: CGPoint(x: 0, y: r)); ctx.strokePath()
for side: CGFloat in [-1, 1] {
    ctx.move(to: CGPoint(x: side * r * 0.64, y: -r * 0.78))
    ctx.addQuadCurve(to: CGPoint(x: side * r * 0.64, y: r * 0.78), control: CGPoint(x: side * r * 0.04, y: 0))
    ctx.strokePath()
}
ctx.restoreGState()
// cyan rim light on the lower-right edge + gloss on the upper-left
ctx.saveGState()
ctx.addEllipse(in: ballRect); ctx.clip()
ctx.setBlendMode(.screen)
ctx.setShadow(offset: .zero, blur: 22, color: rgba(80, 225, 255, 0.9))
ctx.setStrokeColor(rgba(110, 235, 255, 0.6)); ctx.setLineWidth(12)
// shift the outline up-left: only its lower-right edge then falls inside the ball -> a crescent on that side
ctx.strokeEllipse(in: ballRect.offsetBy(dx: -12, dy: 12))
ctx.restoreGState()
ctx.saveGState()
ctx.addEllipse(in: ballRect); ctx.clip()
ctx.drawRadialGradient(grad([rgba(255, 255, 255, 0.55), rgba(255, 255, 255, 0)], [0, 1]),
                       startCenter: CGPoint(x: c.x - 75, y: c.y + 92), startRadius: 0,
                       endCenter: CGPoint(x: c.x - 75, y: c.y + 92), endRadius: 95, options: [])
ctx.restoreGState()

// ── Front half of the rim + embers ──────────────────────────────────────────────────────
ctx.saveGState()
ctx.addPath(tilePath); ctx.clip()
rimPass(front: true)
ctx.setBlendMode(.screen)
for _ in 0..<34 {
    let x = 150 + rnd() * 724, y = 380 + rnd() * 520
    if hypot(x - c.x, y - c.y) < r + 20 { continue }            // not on top of the ball
    let s = 3 + rnd() * 7
    ctx.setShadow(offset: .zero, blur: s * 3, color: rgba(255, 150, 40, 1))
    ctx.setFillColor(rgba(255, lerp(170, 240, rnd()), lerp(60, 160, rnd()), 0.95))
    ctx.fillEllipse(in: CGRect(x: x - s, y: y - s, width: 2 * s, height: 2 * s))
}
ctx.restoreGState()

// vignette + thin edge
ctx.saveGState()
ctx.addPath(tilePath); ctx.clip()
ctx.drawRadialGradient(grad([rgba(0, 0, 0, 0), rgba(0, 0, 0, 0.45)], [0.6, 1]),
                       startCenter: CGPoint(x: 512, y: 540), startRadius: 0,
                       endCenter: CGPoint(x: 512, y: 540), endRadius: 600, options: [.drawsAfterEndLocation])
ctx.restoreGState()
ctx.saveGState()
ctx.addPath(tilePath); ctx.setStrokeColor(rgba(255, 180, 120, 0.16)); ctx.setLineWidth(4); ctx.strokePath()
ctx.restoreGState()

let rep = NSBitmapImageRep(cgImage: ctx.makeImage()!)
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
print("wrote \(out)")
