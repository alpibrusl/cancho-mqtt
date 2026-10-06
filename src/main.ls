edition 5;

// `mqtt` -- an MQTT 3.1.1 broker. Skeleton (issues #2, #9): it listens on the
// port in argv[1], waits on one `Poller`, accepts each connection and closes it.
// It exists so that the authority the compiler derives is the authority the
// broker will have (`docs/design.md` section 2), not the authority of a stub.
// The packets, sessions and queues come with issues #3 to #8.
//
//     mqtt <port>         listen
//     mqtt --authority    print the authority report the build embedded

import std.bytes;
import std.io;
import built;

// A decimal number, or -1 for empty text, a non-digit, or more than 17 digits.
fn number_of[&t](text: &t [byte]) -> [] int {
    if len(text) == 0 || len(text) > 17 {
        return 0 - 1;
    }
    var n = 0;
    var i = 0;
    while i < len(text) {
        let c = int_of(text[i]);
        if c < 48 || c > 57 {
            return 0 - 1;
        }
        n = n * 10 + (c - 48);
        i = i + 1;
    }
    return n;
}

fn serve[&h, &k, &l](heap: &!h Heap, clock: &k Clock, listener: &!l Listener) -> [heap, conn_accept, poll, clock] int {
    match poller_new() {
        Polling::Ok(p) => {
            var poller = p;
            let events = box_slice(heap, 128, 0);
            borrow mut poller as &!pw in {
                poller_add_listener(pw, listener, 0);
            }
            var started = clock_ms(clock);
            var rounds = 0;
            while true {
                var n = 0;
                borrow mut poller as &!pw in {
                    borrow mut events as &!ev in {
                        n = poller_wait(pw, contents(ev), 1000);
                    }
                }
                if n > 0 {
                    match tcp_accept(listener) {
                        Accepted::Ok(c) => {
                            conn_close(c);
                        }
                        Accepted::Again => {
                        }
                        Accepted::Failed(e) => {
                        }
                    }
                }
                rounds = rounds + 1;
            }
            unbox_slice(heap, events);
            poller_close(poller);
            return clock_ms(clock) - started + rounds;
        }
        Polling::Failed(e) => {
            return 4;
        }
    }
}

fn main(world: World) -> [] int {
    let Split { io, ffi, fs, heap, args, net, clock } = split(world);
    release(fs);
    release(ffi);

    var port = 0 - 1;
    var report = false;
    borrow args as &g in {
        if arg_count(g) > 1 {
            if bytes.equal(arg(g, 1), "--authority") {
                report = true;
            } else {
                port = number_of(arg(g, 1));
            }
        }
    }

    var status = 2;
    if report {
        borrow mut io as &!i in {
            io.write_all(i, built.authority());
            io.write_all(i, "\n");
        }
        status = 0;
    } else if port > 0 && port < 65536 {
        status = 3;
        borrow net as &nn in {
            match tcp_listen(nn, port, 128, 0) {
                Listening::Ok(l) => {
                    var listener = l;
                    borrow mut listener as &!lh in {
                        listener_nonblocking(lh);
                        borrow mut heap as &!h in {
                            borrow clock as &c in {
                                status = serve(h, c, lh);
                            }
                        }
                    }
                    listener_close(listener);
                }
                Listening::Failed(e) => {
                }
            }
        }
    }
    release(net);
    release(clock);
    release(args);
    release(io);
    release(heap);
    return status;
}
