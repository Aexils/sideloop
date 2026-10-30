// Serveur anisette NATIF : l'anisette d'un vrai Mac, servie au format anisette-v3.
//
// Depuis le 2026-10-30, GrandSlam refuse toute anisette fabriquée par
// anisette-v3-server (CoreADI Android) — `ec=-80045` dès `o=init`, quel que soit
// le client annoncé. Celle d'un vrai Mac passe : même requête, compte fictif,
// `ec=-20970` (compte inconnu), donc le filtre est franchi. Cf. README.
//
// OTP et machine ID viennent d'AOSKit (`AOSUtilities`), l'identité de l'appareil
// d'AuthKit (`AKDevice`) : deux frameworks privés, chargés à l'exécution. Aucun
// entitlement requis, aucun plugin Mail comme AltServer.
//
// Usage : anisette-server <IP d'écoute> <port> <IP autorisée>...
// N'écoute QUE sur l'IP donnée (l'IP tailnet du Mac) : rien sur le Wi-Fi du
// bureau ni de la maison. Les sources hors liste reçoivent un 403.
import Foundation
import Network
import CryptoKit

dlopen("/System/Library/PrivateFrameworks/AOSKit.framework/AOSKit", RTLD_NOW)
dlopen("/System/Library/PrivateFrameworks/AuthKit.framework/AuthKit", RTLD_NOW)

func log(_ s: String) {
    FileHandle.standardError.write("[\(ISO8601DateFormatter().string(from: Date()))] \(s)\n".data(using: .utf8)!)
}

func call(_ target: AnyObject, _ sel: String, _ arg: AnyObject? = nil) -> AnyObject? {
    let s = NSSelectorFromString(sel)
    guard target.responds(to: s) else { return nil }
    let r = arg == nil ? target.perform(s) : target.perform(s, with: arg)
    return r?.takeUnretainedValue()
}

/// Les en-têtes anisette du moment. L'OTP (`X-Apple-I-MD`) est recalculé à chaque appel.
func anisette() throws -> [String: String] {
    guard let aos = NSClassFromString("AOSUtilities"),
          let otp = call(aos, "retrieveOTPHeadersForDSID:", "-2" as NSString) as? [String: Any],
          let md = otp["X-Apple-MD"] as? String, let mdm = otp["X-Apple-MD-M"] as? String
    else { throw NSError(domain: "anisette", code: 1, userInfo: [NSLocalizedDescriptionKey: "AOSKit n'a pas rendu d'OTP"]) }
    guard let akc = NSClassFromString("AKDevice"), let dev = call(akc, "currentDevice"),
          let udid = call(dev, "uniqueDeviceIdentifier") as? String,
          let serial = call(dev, "serialNumber") as? String,
          let desc = call(dev, "serverFriendlyDescription") as? String
    else { throw NSError(domain: "anisette", code: 2, userInfo: [NSLocalizedDescriptionKey: "AuthKit n'a pas rendu l'identité de l'appareil"]) }

    // Hors d'une app, AuthKit ne connaît pas de bundle : « ((null)/1.0) ». On
    // s'annonce akd, le démon d'authentification de macOS, comme le fait sideloop.
    let client = desc.replacingOccurrences(of: "((null)/1.0)", with: "(com.apple.akd/1.0)")
    let lu = SHA256.hash(data: Data(udid.utf8)).map { String(format: "%02X", $0) }.joined()
    let now = ISO8601DateFormatter()
    now.formatOptions = [.withInternetDateTime]
    return [
        "X-Apple-I-MD": md,
        "X-Apple-I-MD-M": mdm,
        "X-Apple-I-MD-RINFO": "17106176",
        "X-Apple-I-MD-LU": lu,
        "X-Mme-Device-Id": udid,
        "X-Apple-I-SRL-NO": serial,
        "X-MMe-Client-Info": client,
        "X-Apple-I-Client-Time": now.string(from: Date()),
        "X-Apple-I-TimeZone": TimeZone.current.abbreviation() ?? "UTC",
        "X-Apple-Locale": Locale.current.identifier,
    ]
}

func respond(_ conn: NWConnection, _ status: String, _ body: Data) {
    var head = "HTTP/1.1 \(status)\r\nContent-Type: application/json\r\n"
    head += "Content-Length: \(body.count)\r\nConnection: close\r\n\r\n"
    conn.send(content: Data(head.utf8) + body, completion: .contentProcessed { _ in conn.cancel() })
}

let args = CommandLine.arguments
// `--once` : imprime l'anisette et sort (bootstrap 2FA, sans passer par le réseau).
if args.count == 2, args[1] == "--once" {
    do {
        let body = try JSONSerialization.data(withJSONObject: try anisette(), options: [.sortedKeys])
        print(String(data: body, encoding: .utf8)!)
        exit(0)
    } catch { log("anisette indisponible : \(error.localizedDescription)"); exit(1) }
}
guard args.count >= 4, let port = NWEndpoint.Port(args[2]) else {
    log("usage : anisette-server <IP d'écoute> <port> <IP autorisée>...")
    exit(64)
}
let allowed = Set(args[3...])

// Vérifie AVANT d'écouter que les frameworks répondent : un échec ici se lit
// dans le log du LaunchAgent au lieu de servir des 500 à sideloop.
do { _ = try anisette() } catch { log("anisette indisponible : \(error.localizedDescription)"); exit(1) }

let params = NWParameters.tcp
params.requiredLocalEndpoint = .hostPort(host: NWEndpoint.Host(args[1]), port: port)
params.allowLocalEndpointReuse = true
let listener = try NWListener(using: params)

listener.newConnectionHandler = { conn in
    var source = "?"
    if case let .hostPort(host, _) = conn.endpoint { source = "\(host)".components(separatedBy: "%")[0] }
    conn.start(queue: .main)
    conn.receive(minimumIncompleteLength: 1, maximumLength: 16384) { data, _, _, _ in
        let line = data.flatMap { String(data: $0, encoding: .utf8) }?
            .components(separatedBy: "\r\n").first ?? ""
        guard allowed.contains(source) else {
            log("refusé \(source) : \(line)")
            return respond(conn, "403 Forbidden", Data(#"{"error":"source non autorisée"}"#.utf8))
        }
        do {
            let body = try JSONSerialization.data(withJSONObject: try anisette(), options: [.sortedKeys])
            log("servi \(source) : \(line)")
            respond(conn, "200 OK", body)
        } catch {
            log("échec \(source) : \(error.localizedDescription)")
            respond(conn, "500 Internal Server Error", Data(#"{"error":"anisette indisponible"}"#.utf8))
        }
    }
}
listener.stateUpdateHandler = { state in
    switch state {
    case .ready: log("écoute sur \(args[1]):\(args[2]), sources autorisées : \(allowed.sorted())")
    // IP tailnet absente (Tailscale arrêté, Mac qui sort de veille) : on sort,
    // launchd relance avec son délai de KeepAlive.
    case .failed(let e): log("écoute impossible : \(e)"); exit(1)
    default: break
    }
}
listener.start(queue: .main)
dispatchMain()
