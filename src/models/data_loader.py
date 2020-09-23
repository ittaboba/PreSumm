import bisect
import gc
import glob
import random

import torch

from others.logging import logger



class Batch(object):
    def _pad(self, data, pad_id, max_pos):
        rtn_data = [d + [pad_id] * (max_pos - len(d)) for d in data]
        return rtn_data

    def __init__(self, data=None, device=None, is_test=False, max_pos = 512):
        """Create a Batch from a list of examples."""
        if data is not None:
            self.batch_size = len(data)
            pre_src = [x[0] for x in data]
            pre_tgt = [x[1] for x in data]
            pre_segs = [x[2] for x in data]
            pre_clss = [x[3] for x in data]
            pre_src_sent_labels = [x[4] for x in data]

            src = torch.tensor(self._pad(pre_src, 0, max_pos))
            tgt = torch.tensor(self._pad(pre_tgt, 0, max_pos))

            segs = torch.tensor(self._pad(pre_segs, 0, max_pos))
            mask_src = 1 - (src == 0)
            mask_tgt = 1 - (tgt == 0)


            clss = torch.tensor(self._pad(pre_clss, -1, max_pos))
            src_sent_labels = torch.tensor(self._pad(pre_src_sent_labels, 0, max_pos))
            mask_cls = 1 - (clss == -1)
            clss[clss == -1] = 0
            setattr(self, 'clss', clss.to(device))
            setattr(self, 'mask_cls', mask_cls.to(device))
            setattr(self, 'src_sent_labels', src_sent_labels.to(device))


            setattr(self, 'src', src.to(device))
            setattr(self, 'tgt', tgt.to(device))
            setattr(self, 'segs', segs.to(device))
            setattr(self, 'mask_src', mask_src.to(device))
            setattr(self, 'mask_tgt', mask_tgt.to(device))


            if (is_test):
                src_str = [x[-2] for x in data]
                setattr(self, 'src_str', src_str)
                tgt_str = [x[-1] for x in data]
                setattr(self, 'tgt_str', tgt_str)

    def __len__(self):
        return self.batch_size




def load_dataset(args, corpus_type, shuffle):
    """
    Dataset generator. Don't do extra stuff here, like printing,
    because they will be postponed to the first loading time.

    Args:
        corpus_type: 'train' or 'valid'
    Returns:
        A list of dataset, the dataset(s) are lazily loaded.
    """
    assert corpus_type in ["train", "valid", "test"]

    def _lazy_dataset_loader(pt_file, corpus_type):
        dataset = torch.load(pt_file)
        logger.info('Loading %s dataset from %s, number of examples: %d' %
                    (corpus_type, pt_file, len(dataset)))
        return dataset

    # Sort the glob output by file name (by increasing indexes).
    pts = sorted(glob.glob(args.bert_data_path + '.' + corpus_type + '.[0-9]*.pt'))
    if pts:
        if (shuffle):
            random.shuffle(pts)

        for pt in pts:
            yield _lazy_dataset_loader(pt, corpus_type)
    else:
        # Only one inputters.*Dataset, simple!
        pt = args.bert_data_path + '.' + corpus_type + '.pt'
        yield _lazy_dataset_loader(pt, corpus_type)


def abs_batch_size_fn(new, count):
    src, tgt = new[0], new[1]
    global max_n_sents, max_n_tokens, max_size
    if count == 1:
        max_size = 0
        max_n_sents=0
        max_n_tokens=0
    max_n_sents = max(max_n_sents, len(tgt))
    max_size = max(max_size, max_n_sents)
    src_elements = count * max_size
    if (count > 6):
        return src_elements + 1e3
    return src_elements


def ext_batch_size_fn(new, count):
    if (len(new) == 4):
        pass
    src, labels = new[0], new[4]
    global max_n_sents, max_n_tokens, max_size
    if count == 1:
        max_size = 0
        max_n_sents = 0
        max_n_tokens = 0
    max_n_sents = max(max_n_sents, len(src))
    max_size = max(max_size, max_n_sents)
    src_elements = count * max_size
    return src_elements


class Dataloader(object):
    def __init__(self, args, datasets,  batch_size,
                 device, shuffle, is_test):
        self.args = args
        self.datasets = datasets
        self.batch_size = batch_size
        self.device = device
        self.shuffle = shuffle
        self.is_test = is_test
        self.cur_iter = self._next_dataset_iterator(datasets)
        assert self.cur_iter is not None

    def __iter__(self):
        dataset_iter = (d for d in self.datasets)
        while self.cur_iter is not None:
            for batch in self.cur_iter:
                yield batch
            self.cur_iter = self._next_dataset_iterator(dataset_iter)


    def _next_dataset_iterator(self, dataset_iter):
        try:
            # Drop the current dataset for decreasing memory
            if hasattr(self, "cur_dataset"):
                self.cur_dataset = None
                gc.collect()
                del self.cur_dataset
                gc.collect()

            self.cur_dataset = next(dataset_iter)
        except StopIteration:
            return None

        return DataIterator(args = self.args,
            dataset=self.cur_dataset,  batch_size=self.batch_size,
            device=self.device, shuffle=self.shuffle, is_test=self.is_test)


class DataIterator(object):
    def __init__(self, args, dataset,  batch_size, device=None, is_test=False,
                 shuffle=True):
        self.args = args
        self.batch_size, self.is_test, self.dataset = batch_size, is_test, dataset
        self.iterations = 0
        self.device = device
        self.shuffle = shuffle

        self.sort_key = lambda x: len(x[1])

        self._iterations_this_epoch = 0
        if (self.args.task == 'abs'):
            self.batch_size_fn = abs_batch_size_fn
        else:
            self.batch_size_fn = ext_batch_size_fn

    def data(self):
        if self.shuffle:
            random.shuffle(self.dataset)
        xs = self.dataset
        return xs




    def preprocess(self, ex, is_test):
        src = ex['src']
        tgt = ex['tgt'][:self.args.max_tgt_len][:-1]+[2]
        src_sent_labels = ex['src_sent_labels']
        segs = ex['segs']

        if(not self.args.use_interval):
            segs=[0]*len(segs)
        
        clss = ex['clss']
        src_txt = ex['src_txt']
        tgt_txt = ex['tgt_txt']

        inf_ = 0
        sup_ = 1
        LEN_ = len(clss)
        
        #create batch of same sentence by taking window of max_pos
        while(sup_ < LEN_):

            #take and yeld chunk of max_pos token
            if clss[sup_] - clss[inf_] > self.args.max_pos:
                pos_inf, pos_sup = clss[inf_], clss[sup_-1]
                #assign
                src_temp = src[pos_inf:pos_sup]
                segs_temp = segs[pos_inf:pos_sup]
                clss_temp = [x - clss[inf_] for x in clss[inf_:(sup_-1)]]
                src_sent_labels_temp = src_sent_labels[inf_:(sup_-1)]

                #check if to augment dataset by 
                if self.args.augmentation_number is None:
                    inf_ = sup_ - 1

                else:
                    #augment by selecting as new inf_ the middle point (depending by the value of augmentation_number)
                    # inside the interval between (inf_, sup_ - 1)

                    inf_ = int((sup_ - 1 - inf_)/self.args.augmentation_number) + inf_

                if(is_test):
                    yield src_temp, tgt, segs_temp, clss_temp, src_sent_labels_temp, src_txt, tgt_txt
                else:
                    yield src_temp, tgt, segs_temp, clss_temp, src_sent_labels_temp
            
            sup_ += 1


    def batch_buffer(self, data, batch_size):
        minibatch, size_so_far = [], 0
        for ex in data:
            if(len(ex['src'])==0):
                continue
            for chunk in self.preprocess(ex, self.is_test):
                if(chunk is None):
                    continue
                minibatch.append(chunk)
                size_so_far = self.batch_size_fn(chunk, len(minibatch))
                if size_so_far == batch_size:
                    yield minibatch
                    minibatch, size_so_far = [], 0
                elif size_so_far > batch_size:
                    yield minibatch[:-1]
                    minibatch, size_so_far = minibatch[-1:], self.batch_size_fn(chunk, 1)

        if minibatch:
            yield minibatch

    def batch(self, data, batch_size):
        """Yield elements from data until reaching batch_num_elements elements."""
        minibatch = []
        for ex in data:
            minibatch.append(ex)

            if len(minibatch) == self.args.batch_num_elements:
                yield minibatch
                minibatch = []

        if minibatch:
            yield minibatch

    def create_batches(self):
        """ Create batches """
        data = self.data()
        for buffer in self.batch_buffer(data, self.batch_size * 300):

            if (self.args.task == 'abs'):
                p_batch = sorted(buffer, key=lambda x: len(x[2]))
                p_batch = sorted(p_batch, key=lambda x: len(x[1]))
            else:
                p_batch = sorted(buffer, key=lambda x: len(x[2]))

            p_batch = self.batch(p_batch, self.batch_size)


            p_batch = list(p_batch)
            if (self.shuffle):
                random.shuffle(p_batch)
            for b in p_batch:
                if(len(b)==0):
                    continue
                yield b

    def __iter__(self):
        while True:
            self.batches = self.create_batches()
            for idx, minibatch in enumerate(self.batches):
                # fast-forward if loaded from state
                if self._iterations_this_epoch > idx:
                    continue
                self.iterations += 1
                self._iterations_this_epoch += 1
                batch = Batch(minibatch, self.device, self.is_test, self.args.max_pos)

                yield batch
            return

class DataIterator_Test(object):
    def __init__(self, args, dataset, device=None):
        self.args = args
        self.dataset = dataset
        self.iterations = 0
        self.device = device

    def preprocess(self, ex):
        src = ex['src']
        tgt = ex['tgt'][:self.args.max_tgt_len][:-1]+[2]
        src_sent_labels = ex['src_sent_labels']
        segs = ex['segs']

        if(not self.args.use_interval):
            segs=[0]*len(segs)
        
        clss = ex['clss']
        src_txt = ex['src_txt']
        tgt_txt = ex['tgt_txt']

        inf_ = 0
        sup_ = 1
        LEN_ = len(clss)

        #create batch of same sentence by taking window of max_pos
        while(sup_ < LEN_):
            
            #take and yeld chunk of max_pos token
            if clss[sup_] - clss[inf_] > self.args.max_pos:
                # print(inf_, sup_)
                pos_inf, pos_sup = clss[inf_], clss[sup_-1]
                #assign
                src_temp = src[pos_inf:pos_sup]
                segs_temp = segs[pos_inf:pos_sup]
                clss_temp = [x - clss[inf_] for x in clss[inf_:(sup_-1)]]
                src_sent_labels_temp = src_sent_labels[inf_:(sup_-1)]

                inf_ = sup_ - 1

                if len(src_temp) > self.args.max_pos:
                  end_id = [src[-1]]
                  src_temp = src_temp[:(self.args.max_pos - 1)] + end_id
                  segs_temp = segs_temp[:self.args.max_pos]
                  
                  max_sent_id = bisect.bisect_left(clss_temp, self.args.max_pos)
                  
                  src_sent_labels_temp = src_sent_labels_temp[:max_sent_id]
                  clss_temp = clss_temp[:max_sent_id]
                
                yield src_temp, tgt, segs_temp, clss_temp, src_sent_labels_temp, src_txt, tgt_txt
            
            sup_ += 1

        if (LEN_ - 1) > inf_:
          pos_inf = clss[inf_]

          #assign
          src_temp = src[pos_inf:]
          segs_temp = segs[pos_inf:]
          clss_temp = [x - clss[inf_] for x in clss[inf_:]]
          src_sent_labels_temp = src_sent_labels[inf_:]
          
          if len(src_temp) > self.args.max_pos:
            end_id = [src[-1]]
            src_temp = src_temp[:(self.args.max_pos - 1)] + end_id
            segs_temp = segs_temp[:self.args.max_pos]
            
            max_sent_id = bisect.bisect_left(clss_temp, self.args.max_pos)
            src_sent_labels_temp = src_sent_labels_temp[:max_sent_id]
            clss_temp = clss_temp[:max_sent_id]

          yield src_temp, tgt, segs_temp, clss_temp, src_sent_labels_temp, src_txt, tgt_txt

    def __iter__(self):
        while True:
            for ex in self.dataset:
                if(len(ex['src'])==0):
                    continue
                
                chunked_doc = []
                chunked_batch = []

                for chunk in self.preprocess(ex):
                    if len(chunk[0]) > 512:
                      print(chunk)
                    chunked_doc += [chunk]

                    if len(chunked_doc) == self.args.batch_num_elements:

                      chunked_batch += [Batch(chunked_doc, self.device, True, self.args.max_pos)]
                      chunked_doc = []
                  
                if len(chunked_doc) > 0:
                  chunked_batch += [Batch(chunked_doc, self.device, True, self.args.max_pos)]

                yield chunked_batch

            return
            
class Dataloader_Test(object):
    def __init__(self, args, datasets, device):
        self.args = args
        self.datasets = datasets
        self.device = device
        self.cur_iter = self._next_dataset_iterator(datasets)
        assert self.cur_iter is not None

    def __iter__(self):
        dataset_iter = (d for d in self.datasets)
        while self.cur_iter is not None:
            for batch in self.cur_iter:
                yield batch
            self.cur_iter = self._next_dataset_iterator(dataset_iter)


    def _next_dataset_iterator(self, dataset_iter):
        try:
            # Drop the current dataset for decreasing memory
            if hasattr(self, "cur_dataset"):
                self.cur_dataset = None
                gc.collect()
                del self.cur_dataset
                gc.collect()

            self.cur_dataset = next(dataset_iter)
        except StopIteration:
            return None

        return DataIterator_Test(args = self.args, dataset=self.cur_dataset, device=self.device)