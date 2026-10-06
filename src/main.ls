edition 5;

// `mqtt` -- an MQTT 3.1.1 broker. Interim entry point (issue #5): the port is
// argv[1], every bound is its default, the loop runs until killed. The agent-first
// surface (design section 5a) replaces this.

import std.bytes;
import std.io;
import built;
import mqtt.broker;
import mqtt.config;

fn serve[&h, &k, &l, &g](heap: &!h Heap, clock: &k Clock, listener: &!l Listener, cfg: &g [int]) -> [heap, conn_accept, conn_read, conn_write, poll, clock] int {
    match poller_new() {
        Polling::Ok(p) => {
            var b = broker.open(heap, p, listener, cfg);
            while true {
                b = broker.wait(heap, b, clock, listener, 1000);
            }
            broker.close(heap, b);
            return 0;
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
    release(io);

    var port = 1883;
    borrow args as &g in {
        if arg_count(g) > 1 {
            port = config.number(arg(g, 1));
        }
    }
    var status = 2;
    if port > 0 && port < 65536 {
        status = 3;
        borrow net as &nn in {
            match tcp_listen(nn, port, 1024, 1) {
                Listening::Ok(l) => {
                    var listener = l;
                    borrow mut listener as &!lh in {
                        listener_nonblocking(lh);
                        borrow mut heap as &!h in {
                            let cfg = box_slice(h, config.flags(), 0);
                            borrow mut cfg as &!cb in {
                                var i = 0;
                                while i < config.flags() {
                                    contents(cb)[i] = config.default_of(i);
                                    i = i + 1;
                                }
                            }
                            borrow cfg as &cr in {
                                borrow clock as &c in {
                                    status = serve(h, c, lh, contents(cr));
                                }
                            }
                            unbox_slice(h, cfg);
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
    release(heap);
    return status;
}
