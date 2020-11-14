import argparse
from detector.YOLOv4.train import *
if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cfg', type=str, default='models/yolov4l-mish.yaml', help='model.yaml path')
    parser.add_argument('--data', type=str, default='data/coco128.yaml', help='data.yaml path')
    parser.add_argument('--hyp', type=str, default='', help='hyp.yaml path (optional)')
    parser.add_argument('--epochs', type=int, default=300)
    parser.add_argument('--batch-size', type=int, default=16, help="Total batch size for all gpus.")
    parser.add_argument('--img-size', nargs='+', type=int, default=[640, 640], help='train,test sizes')
    parser.add_argument('--rect', action='store_true', help='rectangular training')
    parser.add_argument('--resume', nargs='?', const='get_last', default=False,
                        help='resume from given path/to/last.pt, or most recent run if blank.')
    parser.add_argument('--nosave', action='store_true', help='only save final checkpoint')
    parser.add_argument('--notest', action='store_true', help='only test final epoch')
    parser.add_argument('--noautoanchor', action='store_true', help='disable autoanchor check')
    parser.add_argument('--evolve', action='store_true', help='evolve hyperparameters')
    parser.add_argument('--bucket', type=str, default='', help='gsutil bucket')
    parser.add_argument('--cache-images', action='store_true', help='cache images for faster training')
    parser.add_argument('--weights', type=str, default='', help='initial weights path')
    parser.add_argument('--name', default='', help='renames results.txt to results_name.txt if supplied')
    parser.add_argument('--device', default='', help='cuda device, i.e. 0 or 0,1,2,3 or cpu')
    parser.add_argument('--multi-scale', action='store_true', help='vary img-size +/- 50%%')
    parser.add_argument('--single-cls', action='store_true', help='train as single-class dataset')
    parser.add_argument('--sync-bn', action='store_true', help='use SyncBatchNorm, only available in DDP mode')
    parser.add_argument('--local_rank', type=int, default=-1, help='DDP parameter, do not modify')
    opt = parser.parse_args()

    last = get_latest_run() if opt.resume == 'get_last' else opt.resume  # resume from most recent run
    if last and not opt.weights:
        print(f'Resuming training from {last}')
    opt.weights = last if opt.resume and not opt.weights else opt.weights
    if opt.local_rank in [-1, 0]:
        check_git_status()
    opt.cfg = check_file(opt.cfg)  # check file
    opt.data = check_file(opt.data)  # check file
    if opt.hyp:  # update hyps
        opt.hyp = check_file(opt.hyp)  # check file
        with open(opt.hyp) as f:
            hyp.update(yaml.load(f, Loader=yaml.FullLoader))  # update hyps
    opt.img_size.extend([opt.img_size[-1]] * (2 - len(opt.img_size)))  # extend to 2 sizes (train, test)
    device = torch_utils.select_device(opt.device, apex=mixed_precision, batch_size=opt.batch_size)
    opt.total_batch_size = opt.batch_size
    opt.world_size = 1
    if device.type == 'cpu':
        mixed_precision = False
    elif opt.local_rank != -1:
        # DDP mode
        assert torch.cuda.device_count() > opt.local_rank
        torch.cuda.set_device(opt.local_rank)
        device = torch.device("cuda", opt.local_rank)
        dist.init_process_group(backend='nccl', init_method='env://')  # distributed backend

        opt.world_size = dist.get_world_size()
        assert opt.batch_size % opt.world_size == 0, "Batch size is not a multiple of the number of devices given!"
        opt.batch_size = opt.total_batch_size // opt.world_size
    print(opt)

    # Train
    if not opt.evolve:
        if opt.local_rank in [-1, 0]:
            print('Start Tensorboard with "tensorboard --logdir=runs", view at http://localhost:6006/')
            tb_writer = SummaryWriter(log_dir=increment_dir('/home/user/data1/patrick/expr/yolov4t-visdronedt/exp', opt.name))
        else:
            tb_writer = None
        train(hyp, tb_writer, opt, device)

    # Evolve hyperparameters (optional)
    else:
        assert opt.local_rank == -1, "DDP mode currently not implemented for Evolve!"

        tb_writer = None
        opt.notest, opt.nosave = True, True  # only test/save final epoch
        if opt.bucket:
            os.system('gsutil cp gs://%s/evolve.txt .' % opt.bucket)  # download evolve.txt if exists

        for _ in range(10):  # generations to evolve
            if os.path.exists('evolve.txt'):  # if evolve.txt exists: select best hyps and mutate
                # Select parent(s)
                parent = 'single'  # parent selection method: 'single' or 'weighted'
                x = np.loadtxt('evolve.txt', ndmin=2)
                n = min(5, len(x))  # number of previous results to consider
                x = x[np.argsort(-fitness(x))][:n]  # top n mutations
                w = fitness(x) - fitness(x).min()  # weights
                if parent == 'single' or len(x) == 1:
                    # x = x[random.randint(0, n - 1)]  # random selection
                    x = x[random.choices(range(n), weights=w)[0]]  # weighted selection
                elif parent == 'weighted':
                    x = (x * w.reshape(n, 1)).sum(0) / w.sum()  # weighted combination

                # Mutate
                mp, s = 0.9, 0.2  # mutation probability, sigma
                npr = np.random
                npr.seed(int(time.time()))
                g = np.array([1, 1, 1, 1, 1, 1, 1, 0, .1, 1, 0, 1, 1, 1, 1, 1, 1, 1])  # gains
                ng = len(g)
                v = np.ones(ng)
                while all(v == 1):  # mutate until a change occurs (prevent duplicates)
                    v = (g * (npr.random(ng) < mp) * npr.randn(ng) * npr.random() * s + 1).clip(0.3, 3.0)
                for i, k in enumerate(hyp.keys()):  # plt.hist(v.ravel(), 300)
                    hyp[k] = x[i + 7] * v[i]  # mutate

            # Clip to limits
            keys = ['lr0', 'iou_t', 'momentum', 'weight_decay', 'hsv_s', 'hsv_v', 'translate', 'scale', 'fl_gamma']
            limits = [(1e-5, 1e-2), (0.00, 0.70), (0.60, 0.98), (0, 0.001), (0, .9), (0, .9), (0, .9), (0, .9), (0, 3)]
            for k, v in zip(keys, limits):
                hyp[k] = np.clip(hyp[k], v[0], v[1])

            # Train mutation
            results = train(hyp.copy(), tb_writer, opt, device)

            # Write mutation results
            print_mutation(hyp, results, opt.bucket)

            # Plot results
            # plot_evolution_results(hyp)
