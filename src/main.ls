edition 6;

// `mqtt` -- an MQTT 3.1.1 broker, agent-first (docs/design.md section 5a).
//
//     mqtt serve [--port N] [--max-connections N] ...   run the broker
//     mqtt introspect [--output json]                   what this binary is and can do
//     mqtt skill                                        the same, as an agentskills.io SKILL.md
//     mqtt rules                                        the connection-level rules, as NDJSON
//
// One flag table (`mqtt.config`) drives the parser, `introspect` and `skill`
// (`toolbox.cli`, `toolbox.describe`). A refusal of the process is an error record
// with a stable rule tag and an exit status from the toolbox's table; the log is an
// NDJSON stream on standard output that ends with an `end` record when the broker
// is stopped with SIGINT or SIGTERM.

import std.buffer;
import std.bytes;
import std.io;
import std.json;
import std.signals as sg;
import built;
import toolbox.cli;
import toolbox.describe;
import toolbox.fail;
import toolbox.out;
import mqtt.broker;
import mqtt.config;
import mqtt.logs;
import mqtt.rules;

fn version() -> [] &static [byte] {
    return "0.1.0";
}

// Rules of this tool's own, beside the shared catalogue: `tag|exit|repairable|summary`.
fn own_rules() -> [] &static [byte] {
    return "args.out-of-range|2|sometimes|a bound is below its smallest value or above its ceiling;args.unknown-command|2|never|the first operand is not serve;conflict.address-in-use|5|never|the port is already bound by another program;io.bind-failed|1|never|the operating system refused to listen on the port for a reason that is not permission or a port in use;io.poller-failed|1|never|the operating system could not create the poller or the signal watch";
}

fn tool() -> [] describe.Tool {
    return describe.Tool { name: "mqtt", version: version(), summary: "An MQTT 3.1.1 broker: one poller, memory sized at start, every input bounded, QoS 0 and 1, retained messages, will messages, clean and persistent sessions.", usage: "mqtt serve [--port N] [--max-connections N] [--max-packet N] ... | mqtt introspect [--output json] | mqtt skill | mqtt rules", output: "stream", schema: "mqtt.v1", flags: config.flag_table(), operands: "COMMAND|none|1|1|serve to run the broker; introspect, skill and rules are commands of their own", rules: "args.unknown-flag;args.missing-value;args.bad-value;args.unexpected-value;args.duplicate-flag;args.missing-operand;args.too-many-operands;args.conflict;args.out-of-range;args.unknown-command;conflict.address-in-use;io.permission-denied;io.bind-failed;io.poller-failed;io.write-failed", extra_rules: own_rules(), limits: config.limits(), reversibility: "reversible-cheap", stdin: "no", guarantees: "" };
}

fn embedded() -> [] describe.Built {
    return describe.Built { authority: built.authority(), schema: built.schema(), compiler: built.compiler() };
}

// ---- errors --------------------------------------------------------------

fn problem[&h](heap: &!h Heap, e: fail.Errors, rule: &static [byte], message: &static [byte], hint: &static [byte], key: &static [byte], value: &static [byte]) -> [heap] fail.Errors {
    var w = fail.open_in(heap, own_rules(), rule, message, hint);
    w = fail.no_repair(heap, w);
    w = fail.detail_open(heap, w);
    w = json.put_key(heap, w, key);
    w = json.put_string(heap, w, value);
    return fail.add(heap, e, w);
}

// A flag whose value is outside what the table allows, with a repair that
// replaces the value by the nearest legal one, which is never wider than the ceiling.
fn out_of_range[&h, &g, &p](heap: &!h Heap, e: fail.Errors, args: &g Args, parsed: &p cli.Parsed, i: int, value: int) -> [heap, args] fail.Errors {
    let table = config.flag_table();
    let name = cli.name_of(table, i);
    let low = config.minimum_of(i);
    let high = config.ceiling_of(i);
    var w = fail.open_in(heap, own_rules(), "args.out-of-range", "a flag's value is outside the range this broker accepts", "use a value within the range introspect names for the flag");
    var fixed = high;
    if value < low {
        fixed = low;
    }
    let at = cli.value_index(parsed, table, name);
    var digits = buffer.empty(heap, 20);
    digits = buffer.push_nat(heap, digits, fixed);
    if at >= 0 && cli.value_is_whole(parsed, table, name) {
        borrow digits as &r in {
            w = fail.retry_replacing(heap, w, args, at, buffer.bytes(r));
        }
    } else {
        w = fail.repair_none(heap, w, "write the value as a separate argument, then retry");
    }
    buffer.drop(heap, digits);
    w = fail.detail_open(heap, w);
    w = json.put_key(heap, w, "flag");
    w = json.put_string(heap, w, name);
    w = json.put_key(heap, w, "value");
    w = json.put_int(heap, w, value);
    w = json.put_key(heap, w, "minimum");
    w = json.put_int(heap, w, low);
    w = json.put_key(heap, w, "ceiling");
    w = json.put_int(heap, w, high);
    return fail.add(heap, e, w);
}

// The errors as records, then an `end` record that says the stream did not complete.
// Answers the exit status.
fn refuse[&h, &i](heap: &!h Heap, io: &!i Io, errs: fail.Errors, text_mode: bool) -> [heap, io_write, err_write] int {
    var status = 1;
    var broken = false;
    borrow errs as &er in {
        status = fail.exit_code(er);
        if text_mode {
            out.say_errors(io, "mqtt", er);
        } else {
            broken = !out.error_records(heap, io, er, 0);
        }
        if !broken && !text_mode {
            var w = out.end_open(heap, "mqtt", "mqtt.v1", false, false);
            w = json.put_key(heap, w, "errors");
            w = json.put_int(heap, w, fail.count(er));
            broken = !out.close_line(heap, io, w);
        }
    }
    fail.drop(heap, errs);
    if !broken && !out.flushed(io) {
        broken = true;
    }
    if broken {
        out.write_failed(io, "mqtt");
        return 1;
    }
    return status;
}

// ---- the command line ----------------------------------------------------

// `mqtt rules`: every connection-level rule, then the end record.
fn list_rules[&h, &i](heap: &!h Heap, io: &!i Io, text_mode: bool) -> [heap, io_write, err_write] int {
    var broken = false;
    var r = 0;
    while r < rules.count() && !broken {
        broken = !logs.rule_record(heap, io, text_mode, r);
        r = r + 1;
    }
    if !broken && !text_mode {
        var w = out.end_open(heap, "mqtt", "mqtt.v1", true, true);
        w = json.put_key(heap, w, "rules");
        w = json.put_int(heap, w, rules.count());
        broken = !out.close_line(heap, io, w);
    }
    if !broken && !out.flushed(io) {
        broken = true;
    }
    if broken {
        out.write_failed(io, "mqtt");
        return 1;
    }
    return 0;
}

// Read the command line into `cfg`. Answers -1 to go on and serve, or the exit
// status of what was answered instead (introspect, skill, rules, or refusals).
fn configure[&h, &g, &i, &c](heap: &!h Heap, args: &g Args, io: &!i Io, cfg: &!c [int]) -> [heap, args, io_write, err_write] int {
    let which = cli.subcommand(args);
    if which != 0 {
        return describe.answer(heap, io, which, tool(), embedded());
    }
    let table = config.flag_table();
    let (parsed, found) = cli.parse(heap, args, table, fail.empty_in(heap, own_rules()));
    var e = found;
    var parse_errors = 0;
    var ranged = true;
    var text_mode = false;
    var listing = false;
    borrow e as &er in {
        parse_errors = fail.count(er);
    }
    borrow parsed as &pr in {
        let n = cli.operand_count(pr);
        text_mode = bytes.equal(cli.text(args, pr, table, "format"), "text");
        // An operand error that follows from an unknown flag is not reported twice.
        if parse_errors == 0 {
            if n == 0 {
                e = problem(heap, e, "args.missing-operand", "mqtt needs a command", "mqtt serve, mqtt introspect, mqtt skill or mqtt rules", "operand", "COMMAND");
            } else if bytes.equal(cli.operand(args, pr, 0), "rules") {
                listing = true;
            } else if !bytes.equal(cli.operand(args, pr, 0), "serve") {
                e = problem(heap, e, "args.unknown-command", "the command is not one this broker has", "mqtt serve, mqtt introspect, mqtt skill or mqtt rules", "command", cli.operand(args, pr, 0));
            }
            if n > 1 {
                e = problem(heap, e, "args.too-many-operands", "a command takes no operands", "pass bounds as flags", "operand", "COMMAND");
            }
        }
        var i = 0;
        while i < config.numeric() {
            let v = cli.nat(args, pr, table, cli.name_of(table, i));
            if v < config.minimum_of(i) || v > config.ceiling_of(i) {
                e = out_of_range(heap, e, args, pr, i, v);
                ranged = false;
            }
            cfg[i] = v;
            i = i + 1;
        }
    }
    cli.drop(heap, parsed);
    cfg[config.i_format()] = 0;
    if text_mode {
        cfg[config.i_format()] = 1;
    }
    // A message must fit in a queue beside its entry header and the room kept for
    // control packets.
    if ranged && parse_errors == 0 && cfg[config.i_queue_bytes()] < cfg[config.i_packet()] + 8 + 256 {
        e = problem(heap, e, "args.conflict", "queue-bytes must be at least max-packet + 264, or a packet of the largest size could never be queued", "raise --queue-bytes or lower --max-packet", "flags", "--queue-bytes --max-packet");
    }
    var count = 0;
    borrow e as &er in {
        count = fail.count(er);
    }
    if count > 0 {
        return refuse(heap, io, e, text_mode);
    }
    fail.drop(heap, e);
    if listing {
        return list_rules(heap, io, text_mode);
    }
    return 0 - 1;
}

// ---- serving -------------------------------------------------------------

// Run the broker on `listener` until a stop signal arrives or the log cannot be
// written. Answers the exit status.
fn serve[&h, &g, &k, &l, &w, &i](heap: &!h Heap, cfg: &g [int], clock: &k Clock, listener: &!l Listener, watch: &!w SignalWatch, io: &!i Io) -> [heap, conn_accept, conn_read, conn_write, poll, clock, signals_read, io_write, err_write] int {
    match poller_new() {
        Polling::Ok(p) => {
            let t0 = clock_ms(clock);
            var b = broker.open(heap, p, listener, cfg);
            var token = 0;
            borrow b as &br in {
                token = broker.first_token(br);
            }
            borrow mut b as &!bw in {
                poller_add_signals(broker.poller(bw), watch, token);
            }
            let text = cfg[config.i_format()] == 1;
            var ok = logs.listening(heap, io, text, cfg[config.i_port()], cfg[config.i_connections()], cfg[config.i_packet()], config.memory(cfg), 0);
            let limiter = box_slice(heap, 2 * rules.count(), 0);
            var last_stats = 0;
            var running = ok;
            while running {
                b = broker.wait(heap, b, clock, listener, 1000);
                let ms = clock_ms(clock) - t0;
                let now = ms / 1000;
                borrow mut b as &!bw in {
                    borrow mut limiter as &!lm in {
                        if !logs.drain(heap, io, text, bw, contents(lm), now, ms) {
                            ok = false;
                        }
                    }
                }
                if ok && cfg[config.i_stats()] > 0 && now - last_stats >= cfg[config.i_stats()] {
                    last_stats = now;
                    borrow b as &br in {
                        if !logs.stats(heap, io, text, br, ms) {
                            ok = false;
                        }
                    }
                }
                if sg.any(signals_pending(watch), sg.stop_signals()) {
                    running = false;
                }
                if !ok {
                    running = false;
                }
            }
            var status = 0;
            // Repeats still held back, the counters as they stand, then the end record.
            if ok {
                borrow mut limiter as &!lm in {
                    ok = logs.drain_all(heap, io, text, contents(lm), clock_ms(clock) - t0);
                }
            }
            if ok {
                borrow b as &br in {
                    ok = logs.stats(heap, io, text, br, clock_ms(clock) - t0);
                }
            }
            if ok {
                borrow b as &br in {
                    ok = logs.end_served(heap, io, text, br, clock_ms(clock) - t0);
                }
            }
            if !ok || !out.flushed(io) {
                out.write_failed(io, "mqtt");
                status = 1;
            }
            unbox_slice(heap, limiter);
            broker.close(heap, b);
            return status;
        }
        Polling::Failed(e) => {
            var errs = fail.empty_in(heap, own_rules());
            errs = problem(heap, errs, "io.poller-failed", "the operating system could not create the poller", "", "errno", "poller");
            return refuse(heap, io, errs, cfg[config.i_format()] == 1);
        }
    }
}

// `tcp_listen` failed with `errno`: the port is in use (EADDRINUSE: 98 on Linux, 48
// on macOS), not allowed to this user (EACCES), or something else.
fn listen_failed[&h, &i](heap: &!h Heap, io: &!i Io, port: int, errno: int, text_mode: bool) -> [heap, io_write, err_write] int {
    var rule: &static [byte] = "io.bind-failed";
    var message: &static [byte] = "the operating system refused to listen on the port";
    var hint: &static [byte] = "";
    if errno == 98 || errno == 48 {
        rule = "conflict.address-in-use";
        message = "the port is already bound by another program";
        hint = "stop that program, or choose another port with --port";
    } else if errno == 13 || errno == 1 {
        rule = "io.permission-denied";
        message = "the operating system does not let this user listen on the port";
        hint = "choose a port above 1023 with --port, or run with the privilege that port needs";
    }
    var w = fail.open_in(heap, own_rules(), rule, message, hint);
    w = fail.repair_none(heap, w, "choosing a port is the caller's decision, not a script's");
    w = fail.detail_open(heap, w);
    w = json.put_key(heap, w, "port");
    w = json.put_int(heap, w, port);
    w = json.put_key(heap, w, "errno");
    w = json.put_int(heap, w, errno);
    return refuse(heap, io, fail.add(heap, fail.empty_in(heap, own_rules()), w), text_mode);
}

fn main(world: World) -> [] int {
    let Split { io, ffi, fs, heap, args, net, clock, signals } = split(world);
    // A broker: no files, no foreign code, nothing read from standard input.
    release(ffi);
    release(fs);
    let claim = narrow(signals, "INT,TERM");
    var status = 1;
    borrow mut heap as &!h in {
        borrow args as &g in {
            borrow mut io as &!i in {
                let cfg = box_slice(h, config.flags(), 0);
                var go = 0 - 1;
                borrow mut cfg as &!cb in {
                    go = configure(h, g, i, contents(cb));
                }
                if go >= 0 {
                    status = go;
                } else {
                    borrow cfg as &cr in {
                        let port = contents(cr)[config.i_port()];
                        borrow net as &nn in {
                            match tcp_listen(nn, port, 1024, 0) {
                                Listening::Ok(l) => {
                                    var listener = l;
                                    borrow mut listener as &!lh in {
                                        listener_nonblocking(lh);
                                        borrow claim as &sc in {
                                            match signals_watch(sc) {
                                                Watching::Ok(wt) => {
                                                    var watch = wt;
                                                    borrow mut watch as &!wh in {
                                                        borrow clock as &c in {
                                                            status = serve(h, contents(cr), c, lh, wh, i);
                                                        }
                                                    }
                                                    signals_close(watch);
                                                }
                                                Watching::Failed(e) => {
                                                    var errs = fail.empty_in(h, own_rules());
                                                    errs = problem(h, errs, "io.poller-failed", "the operating system could not watch for stop signals", "", "errno", "signals");
                                                    status = refuse(h, i, errs, contents(cr)[config.i_format()] == 1);
                                                }
                                            }
                                        }
                                    }
                                    listener_close(listener);
                                }
                                Listening::Failed(e) => {
                                    status = listen_failed(h, i, port, e, contents(cr)[config.i_format()] == 1);
                                }
                            }
                        }
                    }
                }
                unbox_slice(h, cfg);
            }
        }
    }
    release(claim);
    release(net);
    release(clock);
    release(args);
    release(io);
    release(heap);
    return status;
}
